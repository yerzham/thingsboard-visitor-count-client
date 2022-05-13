from dotenv import load_dotenv
from typing import Tuple
import threading
import time
from picamera.exc import PiCameraMMALError
from picamera import PiCamera
from picamera.array import PiRGBArray
import multiprocessing
from multiprocessing import Event, Process, Queue, RLock
import sys
import os
import logging
sys.path.append('./utils')
sys.path.append('./lib')

from libs.rtpd.detector import Detector
from utils.tb_device_mqtt import RESULT_CODES, TBDeviceMqttClient

load_dotenv()
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
SERVER = ("tb.yerzham.com", 8883)
PROVISION_DEVICE_KEY = os.getenv('PROVISION_DEVICE_KEY')
PROVISION_DEVICE_SECRET = os.getenv('PROVISION_DEVICE_SECRET')
DEVICE_NAME = os.getenv('DEVICE_NAME')


class RTPDClient:
    @staticmethod
    def _obtain_token(credentials_filename='credentials.txt'):
        try:
            token_file = open(credentials_filename)
        except IOError:
            token: str = TBDeviceMqttClient.provision(
                SERVER[0], PROVISION_DEVICE_KEY, PROVISION_DEVICE_SECRET, SERVER[1], DEVICE_NAME, tls=True)
            if (token):
                with open(credentials_filename, 'w') as token_file:
                    token_file.write(token)
            else:
                token = ''
        else:
            with token_file:
                token = token_file.readline()
        return token

    def __init__(self, server: Tuple[str, int], credentials_filename='credentials.txt'):
        self.token = self._obtain_token(credentials_filename)

        if (not self.token):
            raise Exception("Unable to obtain device token")

        self._client = TBDeviceMqttClient(server[0], self.token, server[1], 1)
        self._detection_queue: Queue[str] = Queue(50)

        self._model_image_dimensions = (544, 320)
        self._model_loc = "models/pd_retail_13/FP16"
        self._detection_threshold = 0.6
        self._camera_framerate = 1  # fps
        self._camera_rotation_degrees = 0
        self._max_detections_to_store = 50  # buffer size

        # Client Operations
        self._operating = False
        self._detection_enabled = False
        self._detecting = False
        self._connected = False
        self._config = None
        # Client Config Validation
        self._detectionEnabled_valid = False
        self._detectionBounds_valid = False
        self._configured = False
        # Client Threads and Processes
        self._connection_thread = None
        self._detection_process = None
        self._detection_stop_event = Event()
        self._detection_started_event = Event()
        self._detection_failed_event = Event()

    def _update_configuration_validity(self):
        self._configured = self._detectionEnabled_valid and self._detectionBounds_valid
        self._client.send_attributes({'configured': self._configured})

    def _set_detection_status(self, detection_status):
        self._detecting = detection_status
        self._client.send_attributes({'detecting': detection_status})

    def _validate_and_read_detectionBounds(self, attributes):
        try:
            if (type(attributes["detectionBounds"]) is list and
                len(attributes["detectionBounds"]) >= 3 and
                all('x' in n and 'y' in n and
                n['x'] <= 1 and n['x'] >= 0 and
                    n['y'] <= 1 and n['y'] >= 0 for n in attributes["detectionBounds"])):
                self._detectionBounds_valid = True
                raw_detection_bounds = []
                for bound in attributes["detectionBounds"]:
                    raw_detection_bounds.append([bound['x'], bound['y']])
                return raw_detection_bounds
            elif (type(attributes["detectionBounds"]) is dict and not attributes["detectionBounds"]):
                self._detectionBounds_valid = True
            else:
                self._detectionBounds_valid = False
            return []
        except (KeyError, TypeError):
            self._detectionBounds_valid = False
            return []

    def _validate_and_read_detectionEnabled(self, attributes):
        try:
            if (type(attributes["detectionEnabled"]) is bool):
                self._detectionEnabled_valid = True
                return attributes["detectionEnabled"]
            else:
                self._detectionEnabled_valid = False
                return False
        except KeyError:
            self._detectionEnabled_valid = False
            return False

    def _validate_and_read_attributes(self, attributes):
        attributes["shared"]["detectionEnabled"] = self._validate_and_read_detectionEnabled(
            attributes["shared"])
        attributes["shared"]["detectionBounds"] = self._validate_and_read_detectionBounds(
            attributes["shared"])
        return attributes

    def _handle_detectionEnabled_change(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config["shared"]["detectionEnabled"] = self._validate_and_read_detectionEnabled(
            result)
        self._update_configuration_validity()

    def _handle_detectionBounds_change(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config["shared"]["detectionBounds"] = self._validate_and_read_detectionBounds(
            result)
        self._update_configuration_validity()

    def _handle_received_attributes(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config = self._validate_and_read_attributes(result)
        self._update_configuration_validity()

    def _request_configuration(self) -> bool:
        self._config = None
        self._client.request_attributes(
            [], ["detectionEnabled", "detectionBounds"], callback=self._handle_received_attributes)
        while (self._config == None):
            time.sleep(0.25)

    def _connected_handler(self, client, userdata, flags, result_code, *extra_params):
        if (result_code != 0):
            log.error("Network: connection failed: %d, %s" % (
                result_code, RESULT_CODES.setdefault(result_code, 'unknown')))
            self._connected = False
            self._config = None
        elif (result_code == 0):
            self._connected = True

    def _detection_process_target(self, max_try=5):
        initalized = False
        while not initalized:
            if (max_try <= 0):
                log.error(
                    "Detection process: failed to initialze deviced for detection")
                self._detection_failed_event.set()
                return
            max_try -= 1
            try:
                # cam setup
                camera = PiCamera()
                camera.resolution = self._model_image_dimensions
                camera.framerate = self._camera_framerate
                # cam capture
                rawCamCapture = PiRGBArray(
                    camera, size=self._model_image_dimensions)
                # detector init
                detector = Detector(model_loc=self._model_loc, model_image_dimensions=self._model_image_dimensions,
                                    detection_threshold=self._detection_threshold, device="MYRIAD")
                detector.set_bounding_points(
                    self._config["shared"]["detectionBounds"])
                initalized = True
            except PiCameraMMALError as err:
                log.warning(
                    "Detection process: failed to initialze PiCamera device. Retrying...")
                camera.close()
                time.sleep(5)
            except RuntimeError as err:
                if (str(err) == "Can not init Myriad device: NC_ERROR"):
                    log.warning(
                        "Detection process: failed to initialze Myriad device. Retrying...")
                    camera.close()
                    time.sleep(5)
                else:
                    log.error(err, exc_info=True)
                    max_try = 0
            except Exception as exc:
                log.error(exc, exc_info=True)
                max_try = 0
                return

        self._detection_started_event.set()
        log.debug("Detection process: PiCamera and MYRIAD device initialized")
        for frame in camera.capture_continuous(rawCamCapture, format="bgr", use_video_port=True):
            if (self._detection_stop_event.is_set()):
                break
            data = detector.process_image(frame.array)
            number_of_people_in_detection_area = len(
                [person for person in data if person["in_bounds"]])
            rawCamCapture.seek(0)
            # load desired data into the queue
            self._detection_to_queue({"ts": int(time.time(
            ) * 1000), "values": {"numberOfPeople": number_of_people_in_detection_area}})

    def _detection_to_queue(self, detection):
        if (self._detection_queue.full()):  # this deals with full queue I think
            self._detection_queue.get()
        self._detection_queue.put_nowait(detection)
        log.debug("Detection process: detection result loaded to queue")

    def _start_detection(self):
        if self._detection_process is not None:
            return False
        self._detection_stop_event.clear()
        self._detection_started_event.clear()
        self._detection_failed_event.clear()
        self._detection_process = Process(
            target=self._detection_process_target)
        self._detection_process.daemon = True
        log.info("Client: starting detection process")
        self._detection_process.start()
        self._detection_enabled = True

    def _stop_detection(self):
        if self._detection_process is None:
            return False
        self._detection_enabled = False
        self._detection_stop_event.set()
        if multiprocessing.current_process() != self._detection_process:
            log.info("Client: stopping detection process")
            self._detection_process.join()
            self._detection_process = None
            log.info("Client: detection process stopped")
        self._set_detection_status(False)

    def _connection_thread_target(self):
        self._client.subscribe_to_attribute(
            'detectionEnabled', self._handle_detectionEnabled_change)
        self._client.subscribe_to_attribute(
            'detectionBounds', self._handle_detectionBounds_change)
        while (self._operating):
            if (self._detection_started_event.is_set()):
                self._set_detection_status(True)
                self._detection_started_event.clear()
            if (self._detection_failed_event.is_set()):
                self._set_detection_status(False)
                break

            if (self._config == None and self._connected == True):
                self._request_configuration()
            elif (self._config != None and self._config["shared"]["detectionEnabled"] == False):
                if (self._detection_enabled == True):
                    log.info("Client: detection disabled")
                    self._stop_detection()
                else:
                    log.debug('Network: idle')
                    self._client.send_attributes({})
                    time.sleep(1.5)
            elif (self._config != None and self._config["shared"]["detectionEnabled"] == True):
                if (self._detection_enabled == False):
                    log.info("Client: detection enabled")
                    self._start_detection()
                else:
                    if (self._detecting):
                        detection_result = self._detection_queue.get()
                        log.debug('Network: sending detection result')
                        self._client.send_telemetry(detection_result)

    def _start_connection(self):
        if self._connection_thread is not None:
            return False
        self._connection_thread = threading.Thread(
            target=self._connection_thread_target)
        self._connection_thread.daemon = True
        self._operating = True
        log.info("Client: starting connection thread")
        self._connection_thread.start()

    def _stop_connection(self):
        if self._connection_thread is None:
            return False
        self._operating = False
        if threading.current_thread() != self._connection_thread:
            log.info("Client: stopping connection thread")
            self._connection_thread.join()
            self._connection_thread = None
            log.info("Client: connection thread stopped")
        if (not self._client.stopped):
            self._client.stop()

    def stop(self):
        self._stop_connection()
        if (not self._client.stopped):
            self._client.stop()
        self._stop_detection()

    def stopped(self):
        return self._client.stopped or self._detection_failed_event.is_set()

    def start(self):
        self._client.connect(
            tls=True, callback=self._connected_handler, keepalive=2)
        self._start_connection()


if __name__ == '__main__':
    rtpd_client = RTPDClient(SERVER)
    try:
        rtpd_client.start()
        while not rtpd_client.stopped():
            time.sleep(1)
        rtpd_client.stop()
    except Exception as ex:
        rtpd_client.stop()
