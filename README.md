## PyAdsMqtt

"Reverse Engineering" of Beckhoffs TCIotCommunicator Protocol

Enables the user to read data points from Twincat 2 & 3 Controllers through ADS and
and publish them over an MQTT Broker. With the underlaying protocol those data points can be viewed and controlled through the 
Beckhoff TwinCAT IoT Communicator App.
All implemented IoT Options (name, unit, limits) are configurable.

ToDo:
 - write values back from app to controller
 - create QR Code on runup -> maybe for an e-ink display
 - broker and controller security
 - setup pi as wifi host
 - implement arrays/structs 

