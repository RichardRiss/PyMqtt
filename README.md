## PyAdsMqtt

"Reverse Engineering" of Beckhoffs TCIotCommunicator Protocol

Enables the user to read data points from Twincat 2 & 3 Controllers through ADS and
and publish them over an MQTT Broker. With the underlaying protocol those data points can be viewed and controlled through the 
Beckhoff TwinCAT IoT Communicator App.
All implemented IoT Options (name, unit, limits) are configurable.

ToDo:
 - write values back from app to controller -> done
 - implement arrays/structs -> done
 - create QR Code on runup -> done
 - print qr code to e-ink display -> done
 - stability test on Pi -> done
 - setup pi as wifi host -> done
 - broker and controller security
 - ability to connect to multiple devices
 - make script pretty again

