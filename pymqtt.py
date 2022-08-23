#!/usr/bin/python
import datetime
import json
import logging
import socket
import time
import os

import paho.mqtt.client as mqtt
import sys
import pyads
import yaml
import threading
from queue import Queue
import qrcode
import netifaces as ni

try:
  from pyroute2 import iproute
  import pyroute2.netlink
  import ipaddr
  import papirus
  import RPi.GPIO as GPIO
except ImportError:
  pass
except AttributeError:
  pass

'''
ToDo:
broker and controller security
setup pi as wifi host

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
    self.struct = None
    self._src_path = None
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
    self.src_path_device = os.path.join(self.src_folder, "device.yaml")
    self.src_path_data = os.path.join(self.src_folder, "data.yaml")

  def read(self, mode, key):
    assert mode
    self._key = key
    if mode == 'device':
      self._src_path = self.src_path_device
    elif mode == 'data':
      self._src_path = self.src_path_data
    with open(self._src_path, 'r', encoding='UTF-8-sig') as f:
      self._ret_val = yaml.safe_load(f)
      if self._ret_val is None:
        return False
      else:
        try:
          return self._ret_val[self._key]
        except KeyError:
          logging.error("Broken Key in Yaml File. Delete " + self._src_path + " and retry.")

  '''
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
  '''

  def get_data(self):
    self.devname = self.read('device', "DeviceName")
    self.updatetime = self.read('device', "UpdateTime")
    self.broker = self.read('device', "Broker")
    self.plc = self.read('device', "PLC")
    if self.plc.get('Mode') == "TC2":
      self.val = self.read('data', "Data_TC2")
    elif self.plc.get('Mode') == "TC3":
      self.val = self.read('data', "Data_TC3")
      self.struct = self.read('data', "Struct")
    else:
      raise KeyError


class Broker:
  _msg_offline = {"Online": False}

  def __init__(self, param: dict = None, devname: str = 'Controller'):
    self.msg = None
    self.msg_offline = None
    self.msg_online = None
    self.notif = None
    self.devname = devname
    self.notifcounter = 1
    self.notifmax = 255
    self.message = ''
    self.param = param
    self._symlist = None

    try:
      self.client = mqtt.Client()
      if self.param.get('User') is not None:
        self.client.username_pw_set(self.param.get('User'), password=self.param.get('Password'))
      self.client.on_connect = self.on_connect
      self.client.on_disconnect = self.on_disconnect
      self.client.on_message = self.on_message
      self.client.connect(self.param.get('IP'), port=self.param.get('Port'), keepalive=60)
      self.client.loop_start()
      self.set_online()
      self.client.will_set(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self._msg_offline), qos=2, retain=True)
      self.clear_notification()

    except:
      logging.error("Error on Initialisation of local MQTT Broker")
      logging.error(f'{sys.exc_info()[1]}')
      logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')

  def on_message(self, client, userdata, msg):
    try:
      logging.info('Topic:' + msg.topic + " Payload: " + str(msg.payload))
      self.msg = json.loads(msg.payload)
      if isinstance(self.symlist, list) and isinstance(self.msg.get('Values'), dict):
        for _key, _val in self.msg.get('Values').items():
          for _sym in (_sym for _sym in self.symlist if isinstance(_sym.get('symlink'), pyads.AdsSymbol)):
            if _key == _sym.get('DisplayName'):
              _symlink: pyads.AdsSymbol = _sym.get('symlink')
              _symlink.write(_val)
              logging.debug(f'{_key} set to value {_val}.')
              self.send_notification(f'{_key} set to value {_val}.')
            elif _key.split('.')[0] == _sym.get('DisplayName'):
              subkey = _key.split('.')[1]
              _symlink: pyads.AdsSymbol = _sym.get('symlink')
              _symlink.value[subkey] = _val
              _symlink.write()
              logging.debug(f'{_key} set to value {_val}.')
              self.send_notification(f'{_key} set to value {_val}.')
    except:
      logging.debug(f'{sys.exc_info()[1]}')
      logging.debug(f'Error on line {sys.exc_info()[-1].tb_lineno}')
      self.client.subscribe(f'//{self.devname}/TcIotCommunicator/Json/Rx/Data')

  def on_connect(self, client, userdata, flags, rc):
    logging.info(f"Device {self.devname} connected to Broker with result code " + str(rc))
    self.client.subscribe(f'//{self.devname}/TcIotCommunicator/Json/Rx/Data')

  def on_disconnect(self):
    self.client.publish(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self._msg_offline), qos=2, retain=True)
    logging.info(f"Device {self.devname} disconnected from Broker.")

  def set_online(self):
    self.client.publish(f'//{self.devname}/TcIotCommunicator/Desc', payload=json.dumps(self.online_message()), qos=2, retain=True)

  @property
  def symlist(self):
    return self._symlist

  @symlist.setter
  def symlist(self, symlist: list):
    self._symlist = symlist

  @symlist.deleter
  def symlist(self):
    self._symlist = None

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

  def __init__(self, cfg_plc: dict, mqttbroker: Broker):
    self._symlist = None
    self._struct = None
    self._symdict = {}
    self._data = []
    self.datasym = []
    self._datapoints = []
    self.connected = False
    self.mode = cfg_plc.get('Mode')
    self._broker = mqttbroker
    self.error = 0
    self.amsnetid = cfg_plc.get('AMSNETID')
    if cfg_plc.get('wifi_connection'):
      self._hostname = f'{ni.ifaddresses("wlan0")[ni.AF_INET][0]["addr"]}'
      self._senderams = f'{self._hostname}.1.1'
    else:
      self._hostname = cfg_plc.get('ETH-IP').split('/')[0]
      self._senderams = cfg_plc.get('Sender-AMS')
    self._user = cfg_plc.get('PLC-User')
    self._pw = cfg_plc.get('PLC-PW')
    self._routename = cfg_plc.get('Routename')
    self._port = None
    self._target_ip = '.'.join(self.amsnetid.split('.')[0:-2])
    if self.mode == 'TC2':
      self._port = 800
    elif self.mode == 'TC3':
      self._port = pyads.PORT_TC3PLC1
    if self._isWin:
      self.plc = pyads.Connection(self.amsnetid, self._port)
    else:
      pyads.open_port()
      if pyads.get_local_address() is None:
        pyads.set_local_address(self._senderams)
      pyads.add_route_to_plc(self._senderams, self._hostname, self._target_ip, self._user, self._pw, route_name=self._routename)
      pyads.close_port()
      self.plc = pyads.Connection(self.amsnetid, self._port, self._target_ip)

  def check_connection(self):
    self.connected = False
    while not self.connected:
      try:
        if not self.plc.is_open:
          self.plc.open()
        self.plc.read_state()
        self._broker.clear_notification()
        self._broker.send_notification(f'Successfully established connection to {self.mode} PLC {self.amsnetid}')
        self.connected = True
      except pyads.ADSError:
        self._broker.send_notification(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        logging.info(f'Unable to establish connection to {self.mode} PLC {self.amsnetid}')
        time.sleep(5)
      except socket.timeout:
        pyads.set_local_address()
        self._broker.send_notification(f'Timeout on connection to {self.mode} PLC {self.amsnetid}. Check Routing parameters.')
        logging.info(f'Timeout on connection to {self.mode} PLC {self.amsnetid}. Check Routing parameters.')
        time.sleep(5)
    return self.connected

  def create_sym_links(self, data: list = None, struct: dict = None) -> list:
    self._data = data
    self._struct = struct
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
          if _var.get('isStruct'):
            _struct = self._struct.get(_var.get("DisplayName"))
            _struct_def = tuple((i.get('DisplayName'), self._datatype.get(i.get('Datatype')), 1) for i in _struct.get('Content'))
            self._symdict = _var | _struct
            self._symdict['symlink'] = self.plc.get_symbol(_struct.get('Path'), structure_def=_struct_def, array_size=_struct.get('Arraysize'))
            self._symlist = self.flatten(self._symdict)
            self.datasym.extend(self._symlist)
          elif isinstance(_var.get("Path"), str):
            self._symdict = _var.copy()
            self._symdict['symlink'] = self.plc.get_symbol(_var.get('Path'))
            self.datasym.append(self._symdict)
      except:
        logging.error(f"Unable to create SymLink to {_var.get('DisplayName')}.")
        logging.error(f'{sys.exc_info()[1]}')
        logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')
        continue
    return self.datasym

  def flatten(self, symdict: dict) -> list:
    _sym: pyads.AdsSymbol = symdict.get('symlink')
    _symlist = []
    if _sym is None:
      return _symlist

    _reg_match = _sym._regex_array.match(_sym.symbol_type)
    if _reg_match is not None:
      _groups = _reg_match.groups()
      _end = int(_groups[1])
      _start = int(_groups[0])
      _struct_def = _sym.structure_def
      _array_size = 1
      _metadata = {var['DisplayName']: {k: v for (k, v) in var.items() if k.startswith('iot.') and v is not None} for var in symdict.get('Content')}
      for _ in range(_start, _end + 1):
        _tempdict = {'DisplayName': symdict.get('DisplayName') + f'[{_}]', 'path': _sym.name + f'[{_}]', 'isStruct': symdict.get('isStruct'), 'struct': _struct_def}
        _tempdict['symlink'] = self.plc.get_symbol(_tempdict['path'], structure_def=_struct_def, array_size=_array_size)
        _tempdict['metadata'] = _metadata
        _symlist.append(_tempdict)
    return _symlist


class TimedThread:
  # Limit parallel Threads
  _jobs = Queue()
  for i in range(10):
    _jobs.put(i)

  def __init__(self, interval, mqttbroker: Broker, symlist: list, plc: PLC):
    self._timer = None
    self.interval = interval
    self.broker = mqttbroker
    self.symlist = symlist.copy()
    self.plc = plc
    self._symlink = None
    self._is_running = False
    self._start_time = time.time()
    self.send_msg = {}
    self._connected = True

  def _run(self):
    self._jobnr = self._jobs.get()
    if self._jobs.empty():
      self.broker.send_notification('Job queue reached its limit! Consider reducing the update time.')
      logging.error('Job queue reached limit. Update time to small')
    self._is_running = False
    self.fetchdata()
    self.publishdata()
    self._jobs.task_done()
    self._jobs.put_nowait(self._jobnr)

  def start(self):
    if not self._is_running and not self._jobs.empty() and self._connected:
      self._start_time += self.interval
      self._timer = threading.Timer(self._start_time - time.time(), self._run)
      self._timer.start()
      self._is_running = True
    elif not self._connected:
      logging.info(f'Trying to reconnected to controller {self.plc.amsnetid}')
      self._connected = self.plc.check_connection()
      if self._connected:
        logging.info(f'Successfully reconnected to controller {self.plc.amsnetid}')
        self.broker.set_online()
        self._start_time = time.time()

  def stop(self):
    self._timer.cancel()
    self._is_running = False

  def fetchdata(self):
    try:
      for _val in self.symlist:
        self._symlink: pyads.AdsSymbol = _val['symlink']
        _val['value'] = self._symlink.read()
      self._connected = True
    except pyads.ADSError:
      self.broker.send_notification('Connection to PLC lost. Trying to reestablish connection.')
      logging.info('Connection to PLC lost. Trying to reestablish connection.')
      self.broker.on_disconnect()
      self._connected = False

  def publishdata(self):
    if self._connected:
      self.send_msg = {
        'Timestamp': datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%f'),
        'GroupName': self.broker.devname,
        'Values': {var['DisplayName']: var['value'] for var in self.symlist},
        'Metadata': self.metagen()
      }
      self.broker.client.publish(f"//{self.broker.devname}/TcIotCommunicator/Json/Tx/Data", payload=json.dumps(self.send_msg), qos=0, retain=True)
      logging.debug(f'send data: \n {self.send_msg}')

  def metagen(self) -> dict:
    _retdict = {}
    for var in self.symlist:
      if var.get('isStruct'):
        for sub in var.get('metadata'):
          _retdict[f'{var.get("DisplayName")}.{sub}'] = var.get('metadata')[sub]
      else:
        _retdict[var.get('DisplayName')] = {k: v for (k, v) in var.items() if k.startswith('iot.') and v is not None}
    return _retdict


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
      logging.FileHandler(filename=os.path.join(folder, 'debug.log'), mode='w', encoding='utf-8'),
      logging.StreamHandler(sys.stdout)
    ])


def device_config(ip: str, plc: dict):
  if ip is None:
    ethip = '.'.join(plc.get('IP').split('.')[0:-1]) + '.1'
    mask = 24
  else:
    ethip, mask = ip.split('/')
  _wlan0_ip = ni.ifaddresses('wlan0')[ni.AF_INET][0]['addr']
  _wlan0_mask = mask_to_cidr(ni.ifaddresses('wlan0')[ni.AF_INET][0]['netmask'])
  with iproute.IPRoute() as ipr:
    index = ipr.link_lookup(ifname='eth0')[0]
    _wifi = ipaddr.IPNetwork(f'{_wlan0_ip}/{_wlan0_mask}')
    _eth = ipaddr.IPNetwork(f'{ethip}/{mask}')
    if not _wifi.overlaps(_eth):
      try:
        # sudo setcap cap_net_admin,cap_net_raw+eip /usr/bin/python3.9
        ipr.addr('add', index, address=ethip, mask=int(mask))
        logging.info(f'device address set to {ethip}/{mask}')
      except pyroute2.netlink.NetlinkError as e:
        if e.code == 17:
          logging.info(f'Tryed to set device IP to {ethip}. Address is already used.')
    else:
      logging.info(f'address conflict between eth0({ethip} and wlan0({_wlan0_ip}). Ethernet IP was not set.)')


def is_raspi():
  _ret = False
  try:
    with open('/sys/firmware/devicetree/base/model', 'r', encoding='UTF-8-sig') as f:
      if 'raspberry pi' in f.read().lower():
        _ret = True
  except Exception as e:
    pass
  logging.info(f'Script running on raspberry pi: {_ret}')
  return _ret


def button_event(channel):
  try:
    _rot = 0
    eth_text = ni.ifaddresses("eth0")[ni.AF_INET][0]["addr"] if ni.AF_INET in ni.ifaddresses("eth0").keys() else 'down'
    wifi_text = ni.ifaddresses("wlan0")[ni.AF_INET][0]["addr"] if ni.AF_INET in ni.ifaddresses("wlan0").keys() else 'down'
    text = papirus.PapirusTextPos(False, rotation=_rot)
    text.AddText(f'PyMQTT Connection:')
    text.AddText(f'Wifi: {wifi_text}', 0, 40, size=16)
    text.AddText(f'Ethernet: {eth_text}', 0, 80, size=16)
    text.WriteAll()
    time.sleep(3)
    print_qr(get_qr_path(), _rot)

  except:
    logging.error(f'Error on Button event channel {channel}.')
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')


def create_button_event() -> None:
  _SW1 = 16
  _SW2 = 26
  _SW3 = 20
  _SW4 = 21

  GPIO.setmode(GPIO.BCM)
  GPIO.setup(_SW1, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
  GPIO.add_event_detect(_SW1, GPIO.RISING, callback=button_event, bouncetime=100)


def get_qr_path() -> str:
  _path = './code.bmp'
  return _path


def create_qr(info: dict) -> None:
  try:
    _rot = 0
    _path = get_qr_path()
    _ip = ni.ifaddresses('wlan0')[ni.AF_INET][0]['addr']
    _port = info.get("Port", "1883")
    _topic = "/"
    _user = info.get("User", "")
    if _user is None:
      _user = ""
    _pw = info.get("Password", "")
    if _pw is None:
      _pw = ""
    _data = f'https://www.desy.de/app?&broker={_ip}&port={_port}&topic={_topic}&user={_user}&password={_pw}'

    # Create image
    qr = qrcode.QRCode(
      version=1,
      error_correction=qrcode.constants.ERROR_CORRECT_L,
      box_size=5,
      border=2,
    )
    qr.add_data(_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(_path)
    print_qr(_path, _rot)

  except:
    logging.error("Unable to create QR Code")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')


def print_qr(path, rot):
  try:
    # Show on PaPiRus Display
    screen = papirus.Papirus(rotation=rot)
    image = papirus.PapirusImage(rotation=rot)
    screen.clear()
    image.write(path)
  except:
    logging.error("Unable to print QR Code")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')


def mask_to_cidr(mask: str):
  return sum(bin(int(x)).count('1') for x in mask.split('.'))


def main():
  init_logging()
  try:
    # Read Yaml File
    cfg = YamlHandler()
    cfg.get_data()
    # Set device configuration
    if is_raspi():
      device_config(cfg.plc.get('ETH-IP'), cfg.plc)
      create_button_event()
      create_qr(cfg.broker)

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
    controller = PLC(cfg.plc, mqttbroker)
    controller.check_connection()
    # Create symbolic Links to Data -> ignore invalid
    symlist = controller.create_sym_links(cfg.val, cfg.struct)
    # Give Broker Write Access
    mqttbroker.symlist = symlist
  except:
    logging.error("Error on Connection with Controller.")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')
    mqttbroker.client.loop_stop()
    mqttbroker.client.disconnect()
    exit(-30)

  try:
    # Start timed Thread which reads plc -> pushes to broker
    tr = TimedThread(cfg.updatetime, mqttbroker, symlist, controller)
    logging.info(f'Starting Cyclic Operation on device {cfg.devname}')
    while True:
      tr.start()

  except:
    logging.error("Error on Cyclic Communication.")
    logging.error(f'{sys.exc_info()[1]}')
    logging.error(f'Error on line {sys.exc_info()[-1].tb_lineno}')
    exit(-40)


if __name__ == '__main__':
  main()
