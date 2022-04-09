import asyncio

from utils.tb_device_mqtt import TBDeviceMqttClient, TBPublishInfo
from lib.rtpd.detector import Detector



telemetry = {"temperature": 41.9, "enabled": False, "currentFirmwareVersion": "v1.2.2"}
client = TBDeviceMqttClient("tb.yerzham.com","kmDcuzPyl7sGirHkYGg1",8883)
# Connect to ThingsBoard
client.connect(tls=True)
# Sending telemetry without checking the delivery status
client.send_telemetry(telemetry) 
# Sending telemetry and checking the delivery status (QoS = 1 by default)
result = client.send_telemetry(telemetry)
# get is a blocking call that awaits delivery status  
success = result.get() == TBPublishInfo.TB_ERR_SUCCESS

print("Success" if success else "Failed")
# Disconnect from ThingsBoard
client.disconnect()