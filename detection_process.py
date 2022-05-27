from lib.rtpd.detector import Detector
from picamera.exc import PiCameraMMALError
from picamera import PiCamera
from picamera.array import PiRGBArray
import multiprocessing
from multiprocessing import Event, Process, Queue, Manager
import sys
import time
import logging
log = logging.getLogger(__name__)

# Import from local folders
sys.path.append('./libs')


class RTPDProcess:
	def __init__(self, detection_queue, detection_areas, detection_threshold=0.6, model_image_dimensions=(544, 320), model_loc=("models/pd_retail_13/FP16/model.xml", "models/pd_retail_13/FP16/model.bin")):
		self._detection_process = None

		# Camera configuration settings
		self._camera_dimensions = (1920,1080)
		self._camera_framerate = 1  # fps

		# Detector initialization variables
		self._model_image_dimensions = model_image_dimensions
		self._model_loc = model_loc
		self._detection_threshold = detection_threshold

		# Detection configuration variables
		self._detection_queue: Queue[str] = detection_queue
		self._detection_areas = detection_areas

		# Detection process status
		self._detection_enabled = False
		self._detecting = False

		# Client operation status events shared between threads and processes
		self._detection_stop_event = Event()
		self._detection_started_event = Event()
		self._detection_failed_event = Event()

	def _detection_process_target(self, detection_areas, max_try=5):
		detection_initialized = False
		camera: PiCamera
		while not detection_initialized:
			if (max_try <= 0):
				log.error(
					"Detection process: failed to initialize device for detection")
				self._detection_failed_event.set()
				return
			max_try -= 1
			try:
				# PiCamera setup
				camera = PiCamera()
				camera.resolution = self._camera_dimensions
				camera.framerate = self._camera_framerate
				# Camera capture
				rawCamCapture = PiRGBArray(
					camera, size=self._camera_dimensions)
				# Detector initialization
				detector = Detector(self._model_loc, self._model_image_dimensions, "MYRIAD")
				detector.set_detection_threshold(self._detection_threshold)
				detector.set_detection_areas([self._detection_areas[0]])
				detection_initialized = True
			except PiCameraMMALError as err:
				log.warning(
					"Detection process: failed to initialize PiCamera device. Retrying...")
				time.sleep(5)
			except RuntimeError as err:
				if (str(err) == "Can not init Myriad device: NC_ERROR"):
					log.warning(
						"Detection process: failed to initialize Myriad device. Retrying...")
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
			detection_data = detector.detect_from_image(frame.array)
			if (detector.get_detection_areas != [self._detection_areas[0]]):
				detector.set_detection_areas([self._detection_areas[0]])
			rawCamCapture.truncate(0)
			# load desired data into the queue
			self._detection_to_queue({"ts": int(time.time(
			) * 1000), "values": {"numberOfPeople": len([person for person in detection_data if person['in_detection_area']])}})


	def _detection_to_queue(self, detection):
		if (self._detection_queue.full()):  # this deals with full queue I think
			self._detection_queue.get()
		self._detection_queue.put_nowait(detection)
		log.debug("Detection process: detection result loaded to queue")


	def start_detection(self):
		if self._detection_process is not None:
			return False
		self._detection_stop_event.clear()
		self._detection_started_event.clear()
		self._detection_failed_event.clear()
		self._detection_process = Process(
			target=self._detection_process_target, args=(self._detection_areas,))
		self._detection_process.daemon = True
		log.info("Client: starting detection process")
		self._detection_process.start()
		self._detection_enabled = True


	def stop_detection(self):
		if self._detection_process is None:
			return False
		self._detection_enabled = False
		self._detection_stop_event.set()
		if multiprocessing.current_process() != self._detection_process:
			log.info("Client: stopping detection process")
			self._detection_process.join()
			self._detection_process = None
			log.info("Client: detection process stopped")

	def enabled(self):
		return self._detection_enabled

	def started(self):
		return self._detection_started_event.is_set()

	def stopped(self):
		return self._detection_stop_event.is_set()

	def failed(self):
		return self._detection_failed_event.is_set()