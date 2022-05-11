from multiprocessing import RLock
import sys, os, logging
import threading
import time
from typing import Tuple
sys.path.insert(0, './utils')
sys.path.insert(0, './lib')

from dotenv import load_dotenv
from utils.tb_device_mqtt import RESULT_CODES, TBDeviceMqttClient, TBPublishInfo

load_dotenv()
logging.basicConfig(level=logging.DEBUG)
SERVER = ("tb.yerzham.com",8883)
PROVISION_DEVICE_KEY = os.getenv('PROVISION_DEVICE_KEY')
PROVISION_DEVICE_SECRET = os.getenv('PROVISION_DEVICE_SECRET')
DEVICE_NAME=os.getenv('DEVICE_NAME')

class RTPDClient:
    @staticmethod
    def _obtain_token(credentials_filename='credentials.txt'):
        try:
            token_file = open(credentials_filename)
        except IOError:
            token: str = TBDeviceMqttClient.provision(SERVER[0], PROVISION_DEVICE_KEY, PROVISION_DEVICE_SECRET, SERVER[1], DEVICE_NAME, tls=True)
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
        # Client Operations
        self._operating = False
        self._connected = False
        self._config = None
        # Client Config Validation
        self._detectionEnabled_valid = False
        self._detectionBounds_valid = False
        self._configured = False
        # Client Threads and Processes
        self._thread = None

    def _update_configuration_validity(self):
        self._configured = self._detectionEnabled_valid and self._detectionBounds_valid
        self._client.send_attributes({'configured': self._configured})

    def _validate_and_read_detectionBounds(self, attributes):
        try:
            if (type(attributes["detectionBounds"]) is list and
                all('x' in n and 'y'in n and 
                n['x'] <= 1 and n['x'] >= 0 and
                n['y'] <= 1 and n['y'] >= 0 for n in attributes["detectionBounds"])):
                self._detectionBounds_valid = True
                return attributes["detectionBounds"]
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
        attributes["shared"]["detectionEnabled"] = self._validate_and_read_detectionEnabled(attributes["shared"])
        attributes["shared"]["detectionBounds"] = self._validate_and_read_detectionBounds(attributes["shared"])
        return attributes

    def _handle_detectionEnabled_change(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config["shared"]["detectionEnabled"] = self._validate_and_read_detectionEnabled(result)
        self._update_configuration_validity()
    
    def _handle_detectionBounds_change(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config["shared"]["detectionBounds"] = self._validate_and_read_detectionBounds(result)
        self._update_configuration_validity()

    def _handle_received_attributes(self, _client, result, exception):
        if exception is not None:
            raise exception
        self._config = self._validate_and_read_attributes(result)
        self._update_configuration_validity()

    def _request_configuration(self) -> bool:
        self._config = None
        self._client.request_attributes([],["detectionEnabled", "detectionBounds"], callback=self._handle_received_attributes)
        while (self._config == None):
            time.sleep(0.25)
    
    def _connected_handler(self, client, userdata, flags, result_code, *extra_params):
        if (result_code != 0):
            print("Connection failed: %d, %s" % (result_code, RESULT_CODES.setdefault(result_code, 'unknown')))
            self._connected = False
            self._config = None
        elif (result_code == 0):
            self._connected = True

    def _thread_main(self):
        self._loop_forever()
    
    def _loop_forever(self):
        self._client.subscribe_to_attribute('detectionEnabled', self._handle_detectionEnabled_change)
        self._client.subscribe_to_attribute('detectionBounds', self._handle_detectionEnabled_change)
        while (self._operating):
            if (self._config == None and self._connected == True):
                self._request_configuration()
            elif (self._config != None and self._config["shared"]["detectionEnabled"] == False):
                print('ping')
                self._client.send_attributes({})
            time.sleep(2)
    
    def _loop_start(self):
        if self._thread is not None:
            return False
        self._thread = threading.Thread(target=self._thread_main)
        self._thread.daemon = True
        self._operating = True
        self._thread.start()

    def _loop_stop(self):
        if self._thread is None:
            return False
        self._operating = False
        if threading.current_thread() != self._thread:
            self._thread.join()
            self._thread = None

    def stop(self):
        self._client.stop()
        self._loop_stop()

    def stopped(self):
        return self._client.stopped

    def connect(self):
        self._client.connect(tls=True, callback=self._connected_handler, keepalive=2)
        self._loop_start()
        


if __name__ == '__main__':
    rtpd_client = RTPDClient(SERVER)
    try:
        rtpd_client.connect()
        while True:
            time.sleep(1000)
    except:
        rtpd_client.stop()