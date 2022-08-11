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
import threading
from queue import Queue

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
    self._value = None
    self._ret_val = None
    self._key = None
    if getattr(sys, 'frozen', False):
      self.src_folder = os.path.dirname(sys.executable)
    else:
      self.src_folder = os.path.dirname(os.path.abspath(__file__))
    self.src_path = self.src_folder + "\\config.yaml"

  def read(self, key):
    self._key = key
    with open(self.src_path, 'r', encoding='UTF-8-sig') as f:
      self._ret_val = yaml.safe_load(f)
      if self._ret_val is None:
        return False
      else:
        try:
          return self._ret_val[self._key]
        except KeyError:
          logging.error("Broken Key in Yaml File. Delete " + self.src_path + " and retry.")

  def add(self, key, value):
    try:
      assert key
      self._key = key
      self._value = value
      with open(self.src_path, 'r') as f:
        yaml_cont = yaml.safe_load(f) or {}
      yaml_cont[self._key] = self._value
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
  _msg_offline = {"Online": False}

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
      self.client.will_set(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self._msg_offline), qos=2, retain=True)
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
    self.client.publish(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self._msg_offline), qos=2, retain=True)

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
  _isWin = False
  if os.name == 'nt':
    _isWin = True

  _datatype = {
    'BOOL': pyads.PLCTYPE_BOOL,
    'BIT': pyads.PLCTYPE_BOOL,
    'BYTE': pyads.PLCTYPE_BYTE,
    'DATA': pyads.PLCTYPE_DATE,
    'DINT': pyads.PLCTYPE_DINT,
    'DT': pyads.PLCTYPE_DT,
    'DWORD': pyads.PLCTYPE_DWORD,
    'INT': pyads.PLCTYPE_INT,
    'LREAL': pyads.PLCTYPE_LREAL,
    'REAL': pyads.PLCTYPE_REAL,
    'SINT': pyads.PLCTYPE_SINT,
    'STRING': pyads.PLCTYPE_STRING,
    'WSTRING': pyads.PLCTYPE_WSTRING,
    'TIME': pyads.PLCTYPE_TIME,
    'TOD': pyads.PLCTYPE_TOD,
    'UDINT': pyads.PLCTYPE_UDINT,
    'UINT': pyads.PLCTYPE_UINT,
    'USINT': pyads.PLCTYPE_USINT,
    'WORD': pyads.PLCTYPE_WORD
  }

  def __init__(self, amsnetid: str, ip: str, mode: str, mqttbroker: Broker):
    self._symdict = {}
    self._data = []
    self.datasym = []
    self._datapoints = []
    self.connected = False
    self.mode = mode
    self._broker = mqttbroker
    self.error = 0
    self.amsnetid = amsnetid
    self._port = None
    self._ip = None
    if self.mode == 'TC2':
      self._port = 800
    elif self.mode == 'TC3':
      self._port = pyads.PORT_TC3PLC1
    if self._isWin:
      self.plc = pyads.Connection(self.amsnetid, self._port)
    else:
      self.plc = pyads.Connection(self.amsnetid, self._port, self._ip)
    self.plc.open()

  def check_connection(self):
    self.connected = False
    if not self.plc.is_open:
      self.plc.open()
    while not self.connected:
      try:
        self.plc.read_state()
        self._broker.clear_notification()
        self._broker.send_notification(f'Successfully established connection to {self.mode} PLC {self.amsnetid}')
        self.connected = True
      except pyads.ADSError:
        self._broker.send_notification(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        logging.info(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        time.sleep(5)

  def create_sym_links(self, data: list = []):
    self._data = data
    self.datasym = []
    for _var in self._data:
      self._symdict = {}
      try:
        if self.mode == 'TC2':
          if isinstance(_var.get('Offset'), int) and _var.get('Datatype') in self._datatype:
            self._symdict = _var.copy()
            self._symdict['symlink'] = self.plc.get_symbol(
              index_group=pyads.INDEXGROUP_MEMORYBIT if _var.get('Datatype') in ['BOOL', 'BIT'] else pyads.INDEXGROUP_MEMORYBYTE,
              index_offset=_var.get('Offset'),
              plc_datatype=self._datatype.get(_var.get('Datatype')))
            self.datasym.append(self._symdict)
        elif self.mode == 'TC3':
          if isinstance(_var.get("Path"), str):
            self._symdict = _var.copy()
            self._symdict['symlink'] = self.plc.get_symbol(_var.get('Path'))
            self.datasym.append(self._symdict)

      except:
        logging.error(f"Unable to create SymLink to {_var.get('DisplayName')}.")
        continue

    return self.datasym


class TimedThread:
  #Limit parallel Threads
  _jobs = Queue()
  for i in range(10):
    _jobs.put(i)

  def __init__(self, interval, mqttbroker: Broker, symlist: list):
    self._timer = None
    self.interval = interval
    self.broker = mqttbroker
    self.symlist = symlist.copy()
    self._symlink = None
    self._is_running = False
    self._start_time = time.time()

  def _run(self):
    _value = self._jobs.get()
    print(_value)
    self._is_running = False
    self.fetchdata()
    self.publishdata()
    self._jobs.task_done()

  def start(self):
    if not self._is_running and not self._jobs.empty():
      self._start_time += self.interval
      self._timer = threading.Timer(self._start_time - time.time(), self._run)
      self._timer.start()
      self._is_running = True

  def stop(self):
    self._timer.cancel()
    self._is_running = False

  def fetchdata(self):
    for _val in self.symlist:
      self._symlink: pyads.AdsSymbol = _val['symlink']
      _val['value'] = self._symlink.read()

  def publishdata(self):
    pass



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
    exit(-10)

  try:
    # Create MQTT Connection
    # Create Device with configured name
    mqttbroker = Broker(cfg.broker, cfg.devname)

  except:
    logging.error("Error on Connection with local MQTT Broker")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')
    exit(-20)

  try:
    # Establish Connection to PLC
    controller = PLC(cfg.plc.get("AMSNETID"), cfg.plc.get("IP"), cfg.plc.get('Mode'), mqttbroker)
    controller.check_connection()
    # Create symbolic Links to Data -> ignore invalid
    symlist = controller.create_sym_links(cfg.val)
  except:
    logging.error("Error on Connection with Controller.")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  try:
    # Start timed Thread which reads plc -> pushes to broker
    tr = TimedThread(cfg.updatetime, mqttbroker, symlist)
    while True:
      tr.start()


  except:
    logging.error("Error on Cyclic Communication.")
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
