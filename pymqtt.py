#!/usr/bin/python
import datetime
import json
import logging
import time
import os
import paho.mqtt.client as mqtt
import sys
import pyads
import yaml

'''
ToDo: 
Find Way to log error -> maybe through MTQQ Object 
What to do on connection loss -> Restart with step 2?

1. Read Yaml File
2. Create MQTT Connection
3. Create Device with configured name
4. Establish Connection to PLC 
5. Create symbolic Links to Data -> ignore invalid
6. Start timed Thread which reads plc -> pushes to broker
7. Select writeable Datapoints
8. Listen to write attempts
'''


class YamlHandler:
  def __init__(self):
    self.val = None
    self.plc = None
    self.broker = None
    self.updatetime = None
    self.mode = None
    self.devname = None
    self.value = None
    self.ret_val = None
    self.key = None
    if getattr(sys, 'frozen', False):
      self.src_folder = os.path.dirname(sys.executable)
    else:
      self.src_folder = os.path.dirname(os.path.abspath(__file__))
    self.src_path = self.src_folder + "\\config.yaml"

  def read(self, key):
    self.key = key
    with open(self.src_path, 'r', encoding='UTF-8-sig') as f:
      self.ret_val = yaml.safe_load(f)
      if self.ret_val is None:
        return False
      else:
        try:
          return self.ret_val[key]
        except KeyError:
          logging.error("Broken Key in Yaml File. Delete " + self.src_path + " and retry.")

  def add(self, key, value):
    try:
      assert key
      self.key = key
      self.value = value
      with open(self.src_path, 'r') as f:
        yaml_cont = yaml.safe_load(f) or {}
      yaml_cont[self.key] = self.value
      with open(self.src_path, 'w') as f:
        yaml.dump(yaml_cont, f)
    except:
      logging.info(f"unable to add {key} to file")
      logging.error(f'{sys.exc_info()[1]}')
      logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  def get_data(self):
    self.devname = self.read("DeviceName")
    self.updatetime = self.read("UpdateTime")
    self.broker = self.read("Broker")
    self.plc = self.read("PLC")
    if self.plc.get('Mode') == "TC2":
      self.val = self.read("Data_TC2")
    elif self.plc.get('Mode') == "TC3":
      self.val = self.read("Data_TC3")
    else:
      raise KeyError


class Broker:
  def __init__(self, param: dict = None, devname: str = 'Controller'):
    self.msg_offline = None
    self.msg_online = None
    self.notif = None
    self.devname = devname
    self.notifcounter = 1
    self.notifmax = 255
    self.message = ''
    self.param = param

    try:
      self.client = mqtt.Client()
      if self.param.get('User') is not None:
        self.client.username_pw_set(self.param.get('User'), password=self.param.get('Password'))
      self.client.on_connect = self.on_connect
      self.client.on_disconnect = self.on_disconnect
      self.client.on_message = self.on_message
      self.client.connect(self.param.get("IP"), port=self.param.get("Port"), keepalive=60)
      self.client.loop_start()
      self.client.publish(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self.online_message()), qos=2, retain=True)
      self.client.will_set(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self.offline_message()), qos=2, retain=True)
      self.clear_notification()

    except:
      logging.error("Error on Initialisation of local MQTT Broker")
      logging.error(f'{sys.exc_info()[1]}')
      logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  def on_message(self, client, userdata, msg):
    print('Topic:' + msg.topic + " Payload: " + str(msg.payload))

  def on_connect(self, client, userdata, flags, rc):
    logging.info("Connected with result code " + str(rc))
    self.client.subscribe(f'//{self.devname}/TcIotCommunicator/Json/Rx/Data')

  def on_disconnect(self):
    self.client.publish(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self.offline_message()), qos=2, retain=True)

  def offline_message(self):
    self.msg_offline = {"Online": False}
    return self.msg_offline

  def online_message(self):
    self.msg_online = {"Timestamp": datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f'), "Online": True}
    return self.msg_online

  def raise_notif_counter(self):
    if self.notifcounter < self.notifmax:
      self.notifcounter += 1
    else:
      self.notifcounter = 1

  def send_notification(self, notif: str):
    self.notif = {"Timestamp": datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f'), "Message": f'{notif}'}
    self.client.publish(f'//{self.devname}/TcIotCommunicator/Messages/{self.notifcounter}', payload=json.dumps(self.notif), qos=2, retain=True)
    self.raise_notif_counter()

  def clear_notification(self):
    for i in range(1, self.notifmax + 1):
      self.client.publish(f'//{self.devname}/TcIotCommunicator/Messages/{i}', payload=json.dumps(''), qos=2, retain=True)
    self.notifcounter = 1

class PLC:
  def __init__(self, amsnetid: str, mode: str):
    self.connected = False
    self.mode = mode
    self.data = []
    self.error = 0
    self.amsnetid = amsnetid
    if self.mode == 'TC2':
      self.plc = pyads.Connection(self.amsnetid, 800)
    elif self.mode == 'TC3':
      self.plc = pyads.Connection(self.amsnetid, pyads.PORT_TC3PLC1)
    self.plc.open()


  def check_connection(self, mqttbroker: Broker):
    self.connected = False
    if not self.plc.is_open:
      self.plc.open()
    while not self.connected:
      try:
        self.plc.read_state()
        mqttbroker.clear_notification()
        mqttbroker.send_notification(f'Successfully established connection to {self.mode} PLC {self.amsnetid}')
        self.connected = True
      except pyads.ADSError:
        mqttbroker.send_notification(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        logging.info(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        time.sleep(5)

  def create_sym_links(self, data: list = []):
    self.data = data


def init_logging():
  log_format = f"%(asctime)s [%(processName)s] [%(name)s] [%(levelname)s] %(message)s"
  log_level = logging.DEBUG
  if getattr(sys, 'frozen', False):
    folder = os.path.dirname(sys.executable)
  else:
    folder = os.path.dirname(os.path.abspath(__file__))
  # noinspection PyArgumentList
  logging.basicConfig(
    format=log_format,
    level=log_level,
    force=True,
    handlers=[
      logging.FileHandler(filename=f'{folder}\\debug.log', mode='w', encoding='utf-8'),
      logging.StreamHandler(sys.stdout)
    ])


def main():
  init_logging()
  try:
    # Read Yaml File
    cfg = YamlHandler()
    cfg.get_data()

  except:
    logging.error("Error occurred with yaml Config File.")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  try:
    # Create MQTT Connection
    # Create Device with configured name
    mqttbroker = Broker(cfg.broker, cfg.devname)

  except:
    logging.error("Error on Connection with local MQTT Broker")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  try:
    # Establish Connection to PLC
    controller = PLC(cfg.plc.get("AMSNETID"), cfg.plc.get('Mode'))
    controller.check_connection(mqttbroker)



  except:
    logging.error("Error on Connection with local MQTT Broker")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  exit()
  bLamp1 = False
  bLamp2 = False
  nTemp = 0.0

  send_msg = {
    'Timestamp': "2022-08-08T21:52:32.166",
    'GroupName': "Fake_CX8190",
    "Values": {"bLamp1": False, "bLamp2": False, "nTemp": 0.0},
    "MetaData": {"bLamp1": {"iot.DisplayName": "Kitchen Lights"},
                 "bLamp2": {"iot.DisplayName": "Living Room Lights"},
                 "nTemp": {"iot.DisplayName": "Outside Temperature",
                           "iot.Unit": "Celsius", "iot.ReadOnly": "true",
                           "iot.MinValue": "5", "iot.MaxValue": "30"}}
  }

  try:
    while True:
      client.publish("test/Fake_CX8190/TcIotCommunicator/Json/Tx/Data", payload=json.dumps(send_msg), qos=0, retain=True)
      time.sleep(1)
  except KeyboardInterrupt:
    client.publish("test/Fake_CX8190/TcIotCommunicator/Desc", payload=json.dumps(msg_offline), qos=2, retain=False)
    time.sleep(1)
    #  client.loop_stop()
    client.disconnect()


if __name__ == '__main__':
  main()
