from json import JSONEncoder
import json
import sys
import time
sys.path.insert(0, './utils')
sys.path.insert(0, './lib')

from utils.tb_device_mqtt import TBDeviceMqttClient, TBPublishInfo
from lib.rtpd.detector import Detector
from multiprocessing import Process, Queue, Event
import cv2

# Configured via "credentials" file. 
# Credentials file will be automatically created when pi connects 
# to the back-end for the first with an appropriate key.
DEVICE_TOKEN = ""

# Hardcoded at the moment. Should be configurable using a config file
SERVER = ("tb.yerzham.com",8883)
DEVICE_ID = 'RTPD Device 1'
VIDEO_INPUT = "video-samples/sample1.mp4"
MODEL_PATH = "./models/intel/person-detection-retail-0013/FP16"
MASK = [[0,0],[1,0],[1,1],[0,1]]
OK_TO_DETECT = True

def run_detection(queue: Queue, detection_stop: Event, n=20, fake=True):
    detection_stop.clear()
    detector = Detector(model_loc=MODEL_PATH)
    cameraSource = cv2.VideoCapture(VIDEO_INPUT)

    try:
        while True:
            if cameraSource.isOpened():
                sourceActive, image = cameraSource.read()
                if sourceActive:
                    detection = detector.process_image(image,verbose=True)
                    if cv2.waitKey(25) & 0xFF == ord('q'):
                        break
                    queue.put({"ts": int(round(time.time() * 1000)),"values": {"numberOfPeople": len(detection)}})
                else:
                    print("source error")
            else:
                cameraSource = cv2.VideoCapture(VIDEO_INPUT)
                print("camera activated")
    finally:
        detection_stop.set()
        cameraSource.release()
        print("detection stopped")



def read_detection_queue(queue: Queue, detection_stop: Event):
    client = TBDeviceMqttClient(SERVER[0],DEVICE_TOKEN,SERVER[1])
    try:
        client.connect(tls=True)
        while (not detection_stop.is_set()):
            if queue.full():
                print("Emptying detection another way because detection is too fast")
                while not queue.empty() and not detection_stop.is_set(): # Empty the queue in bulk. WARNING: if inside is too slow, it might get stuck. FIX IT
                    telemetry = queue.get()
            else:
                time.sleep(1)
                telemetry = queue.get()
                res = client.send_telemetry(telemetry)
                success = res.get() == TBPublishInfo.TB_ERR_SUCCESS
                print("Detection result sent" if success else "Detection result telemetry failed")
    finally:
        client.disconnect()
        print("networking stopped")


if __name__ == '__main__':
    with open("credentials") as file:
        DEVICE_TOKEN = file.read()
        detection_stop = Event()
        detection_queue = Queue(maxsize=10)

        detection_process = Process(target=run_detection, args=(detection_queue,detection_stop))
        detection_read_process = Process(target=read_detection_queue, args=(detection_queue,detection_stop))
        
        detection_read_process.start()
        detection_process.start()

        detection_read_process.join()
        detection_process.join()