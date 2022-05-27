from multiprocessing import Queue, Manager
from dotenv import load_dotenv
from typing import Tuple
import threading
import logging
import time
import sys
import os

# Import from local folders
sys.path.append('./utils')
sys.path.append('./libs')
from lib.rtpd.detector import Detector
from utils.tb_device_mqtt import RESULT_CODES, TBDeviceMqttClient
from detection_process import RTPDProcess

# Prepare environment variables and logger
load_dotenv()
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
# Perpare server connection variables
SERVER = ("tb.yerzham.com", 8883)
PROVISION_DEVICE_KEY = os.getenv('PROVISION_DEVICE_KEY')
PROVISION_DEVICE_SECRET = os.getenv('PROVISION_DEVICE_SECRET')
DEVICE_NAME = os.getenv('DEVICE_NAME')


class RTPDClient:
    def _obtain_token(self, credentials_filename='credentials.txt'):
        """Obtain token via file storing credentials. If not found, use environment variable provisioning 
        credentials to request a new device token. Saves the device token into a file for a later use."""
        try:
            token_file = open(credentials_filename)
        except IOError:
            token: str = TBDeviceMqttClient.provision(
                self._server[0], PROVISION_DEVICE_KEY, PROVISION_DEVICE_SECRET, self._server[1], DEVICE_NAME, tls=True)
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
        """Initialize the RTPD Client. Requires server information and a file where
        where credentials are stored. If credentials do not exist, client requires 
        PROVISION_DEVICE_KEY, PROVISION_DEVICE_SECRET, DEVICE_NAME environment 
        variables defined."""
        self._server = server
        self._token = self._obtain_token(credentials_filename)

        if (not self._token):
            raise Exception("Unable to obtain device token")

        self._client = TBDeviceMqttClient(server[0], self._token, server[1], 1)

        # Client operation status variables
        self._connected = False
        self._operating = False
        self._configured = False
        self._detection_enabled = False
        self._detecting = False
        
        # Client configuration and configuration validation
        self._config = None
        self._detectionEnabled_valid = False
        self._detectionBounds_valid = False

        # Detection variables
        self._max_detections_to_store = 50  # buffer siz
        self._detection_queue: Queue[str] = Queue(self._max_detections_to_store)
        self._detection_areas = Manager().list()

        # Client network thread and detection process
        self._connection_thread = None
        self._RTPD_process = RTPDProcess(self._detection_queue, self._detection_areas, 
            detection_threshold=50, 
            model_image_dimensions=(544, 320), 
            model_loc=("models/pd_retail_13/FP16/model.xml", "models/pd_retail_13/FP16/model.bin"))

    def _send_configuration_validity(self):
        self._configured = self._detectionEnabled_valid and self._detectionBounds_valid
        self._client.send_attributes({'configured': self._configured})

    def _send_detection_status(self, detection_status):
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
        """Callback function that handles received detectionEnabled attribute from an
        attribute subscription"""
        if exception is not None:
            raise exception
        self._config["shared"]["detectionEnabled"] = self._validate_and_read_detectionEnabled(
            result)
        self._send_configuration_validity()

    def _handle_detectionBounds_change(self, _client, result, exception):
        """Callback function that handles received detectionBounds attribute from an
        attribute subscription"""
        if exception is not None:
            raise exception
        self._config["shared"]["detectionBounds"] = self._validate_and_read_detectionBounds(
            result)
        self._detection_areas[:] = []
        self._detection_areas.append(self._config["shared"]["detectionBounds"])
        self._send_configuration_validity()

    def _handle_received_attributes(self, _client, result, exception):
        """Callback function that handles received attributes from a configuration request"""
        if exception is not None:
            raise exception
        self._config = self._validate_and_read_attributes(result)
        self._detection_areas[:] = []
        self._detection_areas.append(self._config["shared"]["detectionBounds"])
        self._send_configuration_validity()

    def _request_configuration(self) -> bool:
        """Sends request to get attribute values"""
        self._config = None
        self._client.request_attributes(
            [], ["detectionEnabled", "detectionBounds"], callback=self._handle_received_attributes)
        while (self._config == None):
            time.sleep(0.25)

    def _connected_handler(self, client, userdata, flags, result_code, *extra_params):
        """Callback function called after ThingsBoard client is connected to MQTTS port.
        If there is a connection error, it resets the configuration for safety in case
        configuration was changed while it was temporarily disconnected"""
        if (result_code != 0):
            log.error("Network: connection failed: %d, %s" % (
                result_code, RESULT_CODES.setdefault(result_code, 'unknown')))
            self._connected = False
            self._config = None
        elif (result_code == 0):
            self._connected = True

    def _connection_thread_target(self):
        """Network connection thread that controls the client depending on network connectivity 
        results and device status. Attributes detectionEnabled and detectionBounds influence
        the device behaviour, while device status triggers sending updates to the server"""
        self._client.subscribe_to_attribute(
            'detectionEnabled', self._handle_detectionEnabled_change)
        self._client.subscribe_to_attribute(
            'detectionBounds', self._handle_detectionBounds_change)
        while (self._operating):
            # Check detection process status updates
            if (self._RTPD_process.started() and not self._detecting):
                self._send_detection_status(True)
            if (self._RTPD_process.stopped() and self._detecting):
                self._send_detection_status(False)
            if (self._RTPD_process.failed()):
                self._send_detection_status(False)
                self._operating=False
                break
            
            # If client configuration lost, request new one
            if (self._config == None and self._connected == True): 
                self._request_configuration()
            # Otherwise, either idle or send detection data
            elif (self._config != None and self._config["shared"]["detectionEnabled"] == False):
                if (self._RTPD_process.enabled() == True):
                    log.info("Client: detection disabled")
                    self._RTPD_process.stop_detection()
                else:
                    log.debug('Network: idle')
                    self._client.send_attributes({})
                    time.sleep(1.5)
            elif (self._config != None and self._config["shared"]["detectionEnabled"] == True):
                if (self._RTPD_process.enabled() == False):
                    log.info("Client: detection enabled")
                    self._RTPD_process.start_detection()
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
        self._RTPD_process.stop_detection()

    def stopped(self):
        return not self._operating or self._client.stopped or self._RTPD_process.failed()

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
        print(ex)
        rtpd_client.stop()
