# RTPD Client

Real-Time Person Detection Client responsible for ThingsBoard device provisioning, data stream, and device control.

## Description
The detector device client implementation. Client connects to ThingsBoard server using MQTT protocol, then:

1. Reads configuration attributes
2. Validates configuration
3. Sends confiuration validity status
4. Subsribes to configuration updates
5. Start a connection thread
6. Controls detection process based on the configuration

In the background:

* Handles connection status and configuration updates
* Validates configuration updates
* Sends configuration validity and detection process status updates
* Reconnects to the back-end and updates configuration when an internet connection is back

## Installation
```
$ git clone https://version.aalto.fi/gitlab/dtap1/rtpd-client.git
$ cd rtpd-client
$ ./import-detector.sh
```
**Note:** $ `import-detector.sh` only works with `bash` installed. If on Windows, use Git Bash to execute or run the equivalent commands for your system.
### Program set up
```
$ python3 -m venv venv
$ source ./venv/bin/activate
(venv) $ python3 -m pip install -r requirements.txt
```
It may take a while to download and compile `numpy`. Some Raspberry Pi may not have sufficient memory. If you have it installed on another (global or user) environment, consider installing `requirements.txt` on the environment with `numpy` compiled. Version of `numpy` is not specified to allow a quick download from cache. If there are incompatibility issues, consider specifying `numpy` version `1.21.6`. Alternatively, remove all version specifications from `requirements.txt`.

### Environment set up
#### For a new device provisioning
```
$ touch .env
```
In the `.env` file that you created in the project root directory, specify:
```
PROVISION_DEVICE_KEY=[ThingsBoard Device Profile provisioning key]
PROVISION_DEVICE_SECRET=[ThingsBoard Device Profile provisioning secret]
DEVICE_NAME=[ThingsBoard new device name]
```
Alternatively, you can specify these values in your system environment variables.

Ask `PROVISION_DEVICE_KEY` and `PROVISION_DEVICE_SECRET` information from ThingsBoard system administrator or tenant.

#### For existing device authorization
```
$ touch credentials.txt
```
In the `credentials.txt` file that you created in the project root directory, specify: 
```
[Device Token]
```
Ask for Device Token information from ThingsBoard system administrator or tenant.

#### Server configuration
Client software is hardcoded to connect to host `tb.yerzham.com`. It is also hardcoded to use TLS encryption for MQTT communiation.

If you wish to use your own ThingsBoard server, update the `SERVER` variable in `main.py`. If you do not wish to use MQTT over TLS, set `use_tls` variable in `main.py` to `False` (do not forget to use a corresponding protocol port). 

Instructions to set up your ThingsBoard server:

* Installation: https://thingsboard.io/docs/user-guide/install/installation-options/
* MQTT configuration parameters: https://thingsboard.io/docs/user-guide/install/config/#local-mqtt-transport-parameters

## Usage
To run the client:
```
(venv) $ python3 main.py
```
After the client software is running, use our custom web application or ThingsBoard dashboard to control the device. If you have your own ThingsBoard server, follow our instruction on how to configure the available devices in ThingsBoard.

## Support
Open a new Issue in this repository or contact the authors/contributors.

## Roadmap and Contributing
If there is a commercial interest to fully develop the system, please contact the authors/contributors. Otherwise, you are free to offer your own contributions or fork this repository.


## Authors and acknowledgment
Yerzhan Zhamashev: ThingsBoard MQTT communication, process and thread management, shared resources and queue, state control and updates.

Aaron Campbell: Detector class implementation, fixes in Detection Process.

## License
Copyright 2022. Yerzhan Zhamashev, Aaron Campbell

Licensed under the GNU General Public License version 3 (the "License"); you may not use files in this directory except in compliance with the License or if otherwise stated in the files. You may obtain a copy of the License at

https://opensource.org/licenses/GPL-3.0

Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

## Project status
Development stopped and repository is not maintained. See "Roadmap and Contributing" section of the README.md.
