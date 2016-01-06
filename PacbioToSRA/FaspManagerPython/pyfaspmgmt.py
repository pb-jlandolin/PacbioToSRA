import conf
import log
import signal
import time
import socket 
import os
import itertools
import tempfile
import subprocess
import Queue
import pprint

from threading import Thread

faspMgmtPort=0
faspMgmtSock=None
running=True
transferId=itertools.count()

class EnumSuite(set):
    def __getattr__(self, name):
        if name in self:
            return name
        raise AttributeError

ASPERA_ENV = EnumSuite(['ASPERA_SCP_PASS', 
                        'ASPERA_SCP_COOKIE', 
                        'ASPERA_SCP_TOKEN', 
                        'ASPERA_SCP_FILEPASS',
                        'ASPERA_SCP_LICENSE', 
                        'ASPERA_SCP_DOCROOT'])

activeTransfers={}

def mgmt_connection( sock ):
  sock.settimeout(.1)
  notifications=[]
  uid = ""
  sessionActive = True

  print "Started mgmt_connection"

  closeMgmt = False

  while 1:
    msg = {}
    if (notifications): 
      if 'FASP' not in notifications[0]:
	log.err("BAD notif: %s", notifications[0])
	notifications=notifications[i+1:]
      for i in range( 1, len(notifications) ):
        try: 
          line = notifications[i].strip()
        except:
          continue
        if line: 
          try: 
            k,v = line.split(": ", 1)
          except:
            log.dbg("Bad line: %s" % line)
            continue
          msg[k]=v
        else:
          notifications=notifications[i+1:]
          log.dbg("Got message from ASCP %s" % pprint.pformat(msg))
          try:
            activeTransfers[msg['UserStr']][0].put(msg)
          except:
            try:
              # Create two queues, one for messages received from fasp mgmt,
              # the other for messages to send to fasp mgmt.
              activeTransfers[msg['UserStr']] = ( Queue.Queue(), Queue.Queue() )
              activeTransfers[msg['UserStr']][0].put(msg)
              uid = msg['UserStr']
            except Exception, e:
              log.info("No UserId specified for transfer, closing FaspMgmt." )
              sessionActive = False
          continue # Parsing notifications until all notifications consumed.
          

    if uid:
      msg = None
      try: 
        msg = activeTransfers[uid][1].get_nowait()
      except:
        pass
      if msg:
        log.dbg("Sending msg to ascp '%s'" % msg)
        sock.sendall(msg.encode("utf-8"))
        continue
          
    if closeMgmt:
      print "Closing mgmt connection; socket closed"     
      log.dbg("Closing mgmt connection; socket closed")     
      return

    gotData = False
    gotTimeout = False
    h = ""
    try: 
      h = sock.recv(16384).decode("utf-8")
      log.dbg("Received raw FASP message: %s" % h)
      notifications += h.splitlines()
      if h:
        gotData = True
    except socket.timeout, e:
      gotTimeout = True
      pass
    except Exception, e:
      log.dbg("Closing mgmt connection; %s" % e)
      sessionActive = False
      return

    if not gotData and not gotTimeout:
      closeMgmt = True

def ascp_thread( args, env, queue ):
  try: 
    env.update(os.environ)  
    output = subprocess.check_output( args, shell=False, env=env, stderr=subprocess.STDOUT )
    queue.put(None)
    log.dbg("ASCP exited normally (%s)" % output.strip())
  except subprocess.CalledProcessError, e:
    msg = "ASCP exited with errorcode=%d. " % e.returncode
    if e.output.strip():
      msg = "%s; Error was \"%s\"" % ( msg, e.output.strip() )
    log.info(msg)
    queue.put(msg)
  except Exception, e:
    msg = "Error calling ASCP; %s" % e
    log.err(msg)
    queue.put(msg)


class Listener:
  s = None
  fun = None
  t = []

  def __init__(self, host, port, function):
    self.s = socket.socket()
    self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.s.bind( (host, port) )
    self.s.listen(500)
    self.fun = function

  def accept(self):
    try:
      newsock = self.s.accept()
      log.dbg("Received connection from %s" % newsock[1][0])
      thread = Thread (target=self.fun, args=(newsock[0],) )
      thread.daemon = True
      thread.start()
    except Exception, e:
      log.dbg( "On Receive: %s" % e )


class FaspTransfer (object):
  """
  A python FASP Management Interface.
  """


  s = None
  fun = None
  sId = None 
  dirty = True
  ident = "Not Assigned"
  stat = "Not Started"
  files_complete = []
  errs = []
  pct = 0
  totalBytes = 0
  keepalive = False
  ascp_isrunning = False
  files = []

  def build_path( self, src, dst, old, user, remote_host, args, dest_dir=".", upload=True ):
    if not old:
      if src:
        with tempfile.NamedTemporaryFile( delete=False ) as tmp:
          fi_list = []
          if src and dst:
            args.append("--file-pair-list") 
            for i in zip(src, dst):
              fi_list = fi_list + list(i)
          else:
            args.append("--file-list") 
            fi_list = src
          tmp.write("\n".join(fi_list).encode("utf-8"))
          self.file_list=tmp.name
          args.append(self.file_list)

      args.append ("%s" % dest_dir)
    else:
      if upload:
        if src:
          for i in src:
            args.append(i)
        args.append ("%s@%s:%s" % (user, remote_host, dest_dir) )
      else:
        if src:
          for i in src:
            args.append ("%s@%s:%s" % (user, remote_host, i) ) 
        args.append ("%s" % dest_dir)


  def __init__(self, 
    user, remote_host, src_files=[], password="", key_file="", remote_tcp_port=0, remote_fasp_port=0, 
    dest_files=[], dest_dir=".", 
    target_rate=0,
    cookie="", token="", ear_passphrase="", 
    upload=True, persistent=False,
    args=[], old=False
  ):
    """ Initialize a FASP Session ( user, destination )
    optional arguments:  password, key_file, remote_port, src_files, dest_dir,
                         args (command line arguments as a list), 
                         persistent, upload
    Examples: 

      Starting a regular session:

      session = FaspTransfer( "root", 
                          "dest.example.com", 
                          src_files = [ 'one, 'two' ],
                          password = "Secret"
                        )

      Starting a persistent session:
      
      session = FaspTransfer( "root", "dest.example.com", 
                          persistent=True, password="Secret" )

      Starting a download session:

      session = FaspTransfer( "root", 
                    "dest.example.com", 
                    src_files = [ 'one, 'two' ],
                    password = "Secret",
                    upload=False
                  )
 
    """
    self.files += src_files
    self.remote_host = remote_host
    env = {}
    self.sId = transferId.next()
    self.stat = "Not Started"
    self.totalBytes = 0
    self.keepalive = False
    self.upload = upload

    if old and persistent:
      raise ValueError("Upgrade ascp to 3.x and higher to use persistent mode")

    if not src_files and not persistent:
      raise ValueError("No files specified")

    if src_files and persistent:
      raise ValueError("Can not specify src_files with persistent")

    args = [ conf.cfg.get("all", "ascpPath") ] + args

    if remote_tcp_port:
      args.append("-P")
      args.append(str(remote_tcp_port))

    if remote_fasp_port:
      args.append("-O")
      args.append(str(remote_fasp_port))

    if target_rate:
      args.append("-l")
      args.append(str(target_rate))

    if (key_file): 
      args.append("-i")
      args.append(key_file)

    if (password):
      env[ASPERA_ENV.ASPERA_SCP_PASS]=password

    if (persistent):
      args.append('--keepalive')
      self.keepalive = True
      # For 2.x;
      #args.append('--mode')
      #args.append('send')

    if cookie:
      env[ASPERA_ENV.ASPERA_SCP_COOKIE] = cookie

    if token:
      env[ASPERA_ENV.ASPERA_SCP_TOKEN] = token

    if ear_passphrase:
      env[ASPERA_ENV.ASPERA_SCP_FILEPASS] = ear_passphrase
      args.append("--file-crypt")
      if self.upload:
        args.append("encrypt")
      else:
        args.append("decrypt")
      

    args.append("-q")
    args.append("-M")
    if not faspMgmtPort:
      depth = 0
      while not faspMgmtPort:
        time.sleep(0.01)
        depth += 1
        if (depth > 100):
          raise ValueError("Could not get Fasp Management Port")
    args.append(str(faspMgmtPort))
    args.append("-u")
    self.ident= "pyFM_%s.%x.%x" % ( conf.cfg.get("all", "sysId"),
                                     int(conf.cfg.get("all", "sessionNo")),
                                     self.sId  )
    args.append(self.ident)
    # for 2.x;
    #args.append("%s@%s:%s" % (user, remote_host, dest_dir) )  

    # for 3.x;
    if not old:
      args.append('--mode')
      if self.upload:
        args.append('SEND')
      else:
        args.append('RECV')
      args.append('--host')
      args.append(remote_host)
      args.append('--user')
      args.append(user)

    self.build_path( src_files, dest_files, old, user, remote_host, args, dest_dir, self.upload )

    log.info("(%s) Calling ascp with %s" % (self.ident, " ".join(args)) )
    log.info("(%s) File list = [%s]" % (self.ident, ",".join(src_files)) )

    self.ascp = Queue.Queue()
    self.ascp_isrunning=True
    thread = Thread( target=ascp_thread, args = (args, env, self.ascp) )
    thread.daemon = True
    thread.start()

  def consume_msg(self):
    try: 
      msg = activeTransfers[self.ident][0].get_nowait()
    except Exception, e:
      #log.dbg("consume_msg: %s" % e)
      return # No messages to consume
    msgTypes = ( "INIT", "SESSION", "STATS", "STOP", "DONE", "FILEERROR", "ERROR", "NOTIFICATION" )
    log.dbg("Got message of type %s" % msg["Type"] )

    if (msg["Type"] not in msgTypes):
      log.dbg("Ignoring message")
      return self.consume_msg()

    if msg["Type"] in ( "NOTIFICATION" ):
      self.notif = msg
      try:
        self.totalBytes += int(msg["PreTransferBytes"])
      except:
        pass

    if msg["Type"] in ( "INIT", "SESSION", "DONE", "ERROR" ):
      self.stat = msg["Type"].strip()

    if msg["Type"] in ("DONE", "ERROR"):
      log.info("(%s) Session Complete" % self.ident)
      self.end = msg

    if msg["Type"] in ( "SESSION" ):
      self.params = msg

    if msg["Type"] in ( "ERROR", "FILEERROR" ):
      if msg["Type"] == "FILEERROR":
        self.errs.append( "File '%s' %s" % (msg["File"], msg["Description"]) )
      else:
        log.info("(%s) Session Failure: %s" % (self.ident, msg["Description"]) )
        self.errs.append( "Session failed: %s" % (msg["Description"]) )
      
    if msg["Type"] in ( "STOP" ):
      self.files_complete.append(msg["File"])
      try: 
        self.transfersAttempted = msg["TransfersAttempted"]
        self.transfersPassed = msg["TransfersPassed"] 
      except:
        pass


    if msg["Type"] in ( "STATS" ):
      self.stats = msg

    self.consume_msg()
    

  def status(self):
    "Returns the current session state, ( INIT, SESSION, DONE, or ERROR )."
    log.dbg("Doing self.status (%s)" % self.stat )
    self.consume_msg()

    try: 
      res = self.ascp.get_nowait()
      self.ascp_isrunning = False

      if res:
        self.stat = "ERROR"
        self.errs.append(res)

      if res == None:
        # Make sure we have consumed all messages.
        time.sleep(0.2)
        self.consume_msg()

        if self.stat in ("ERROR", "DONE"):
          return self.stat

        try: 
          if self.transfersAttempted == self.transfersPassed:
            log.dbg("Setting state to DONE based on file success")
            "This is a work around for ASCP4 which does not always print DONE"
            self.stat = "DONE"
          return self.stat
        except:
          log.dbg("Could not get completed file count")
        log.dbg("Setting state to error based on inconclusive xfer results")
        self.errs.append("DONE/ERROR status message not recieved")
        self.stat = "ERROR"
      else:
        log.dbg("Unexpected Queue Value.")
 
    except:
      pass

    return self.stat

  def getParam(self, param):
    "Get a identifier from SESSION notification."
    try:
      self.params["param"]
    except:
      return ""

  def addFile(self, src, dest, depth=0):
    self.files.append(src)
    "If persistent sessions is enabled transfer src and name it dest"
    if not self.keepalive:
      raise ValueError("Start a persistent session to enable addFile support")

    msg = \
"""FASPMGR 2
Type: START
Source: %s
Destination: %s

""" % (src, dest)
    try:
      activeTransfers[self.ident][1].put(msg)
    except Exception, e:
      if (depth > 10):
        raise e
      time.sleep(0.1)
      self.addFile(src,dest,depth+1)


  def closePersistentSession( self ):
    "Close persistent session. Use getStatus to check if session has completed."
    if not self.keepalive:
      raise ValueError("Session is not a persistent session")
    msg = \
"""FASPMGR 2
Type: DONE
Operation: Linger

"""
    activeTransfers[self.ident][1].put(msg)
 
  def pctComplete(self):
    "If precompute is enabled return the percent complete from 0 to 100"
    if self.status() == "DONE":
      return 100

    try:
      pct = (int(self.stats["FileBytes"]) * 100) / self.totalBytes  
      if pct > 100:
        pct = 99
      return pct
    except Exception, e:
      return 0

  def isRunning(self):
    if self.status() in ("DONE", "ERROR"):
      return False
    return True

  def cancel(self):
    if not self.isRunning():
      return False
      
    try:
      # Request that ASCP kill itself.
      msg = \
"""FASPMGR 2
Type: CANCEL

"""
      activeTransfers[self.ident][1].put(msg)
      depth = 0
      while (depth < 10):
        if not self.isRunning(): 
          return True
        time.sleep(0.5)
        depth += 1
      return False
    except Exception, e:
      log.dbg("Got an error trying to kill ASCP: %s" % e)
      pass


  def cleanup(self):

    log.dbg("Called cleanup on %s" % self.ident)
    if self.dirty:
      self.cancel()
      try:
        self.dirty = False
        try:
          if self.file_list:
            pass
            #os.remove(self.file_list)
        except:
          pass
        self.ascp.kill()
         
      except:
        pass
     

  def close(self):
    "Terminate all resources associated with transfer including active transfer."
    self.cleanup()

  def __del__(self):
    self.cleanup()

  def __exit__(self, type, value, traceback):
    self.cleanup()




def start_mgmt( pyFaspMgmtPort=33500, pyFaspMgmtHost="localhost", recursionLevel=0):
  listener = ""
  if recursionLevel > 50:
    log.err("Error binding to port")
    raise ValueError("Could not bind to pyFaspMgmt port")

  try:
    pyFaspMgmtPort = conf.cfg.get("all", "mgmtPort")
    pyFaspMgmtHost = conf.cfg.get("all", "mgmtHost")
  except:
    pass
  
  try:
    listener = Listener( pyFaspMgmtHost, pyFaspMgmtPort, mgmt_connection )
  except:
    return start_mgmt ( pyFaspMgmtPort + 1, pyFaspMgmtHost, recursionLevel+1 )

  log.log("Starting pyFaspMgmt git %s built %s on port %d" % (conf.VERSION, conf.DATE, pyFaspMgmtPort), "info")

  global faspMgmtPort, faspMgmtSock
  faspMgmtSock = listener
  faspMgmtPort = pyFaspMgmtPort

  while running:
    listener.accept( )


def ctrlc_handler(signal, frame):
  print "Ctrl-C received."
  fasp_exit()

  print "Exiting... "
  os._exit(0) 

def fasp_exit():      
  global running
  running = False
  time.sleep(.1)

  if (faspMgmtSock):
    faspMgmtSock.s.shutdown(socket.SHUT_RDWR) 
    faspMgmtSock.s.close()

  try:
    mgmt_thread.exit()
  except:
    pass

class FaspSend (FaspTransfer):
  def __init__(self,
    user, remote_host, src_files=[], password="", key_file="", 
    remote_tcp_port=0, remote_fasp_port=0, 
    dest_files=[], dest_dir=".", 
    target_rate=0,
    cookie="", token="", ear_passphrase="", 
    persistent=False,
    args=[], old=False):
    FaspTransfer.__init__(self, user, remote_host, src_files=src_files, password=password, key_file=key_file, 
      remote_tcp_port=remote_tcp_port, remote_fasp_port=remote_fasp_port, dest_files=dest_files, dest_dir=dest_dir, 
      target_rate=target_rate, cookie=cookie, 
      token=token, ear_passphrase=ear_passphrase, upload=True, persistent=persistent, args=args, old=old)


class FaspReceive (FaspTransfer):
  def __init__(self,
    user, remote_host, src_files=[], password="", key_file="", 
    remote_tcp_port=0, remote_fasp_port=0, 
    dest_files=[], dest_dir=".", 
    target_rate=0,
    cookie="", token="", ear_passphrase="", 
    persistent=False,
    args=[], old=False):
    FaspTransfer.__init__(self, user, remote_host, src_files=src_files, password=password, key_file=key_file, 
      remote_tcp_port=remote_tcp_port, remote_fasp_port=remote_fasp_port, dest_files=dest_files, dest_dir=dest_dir, 
      target_rate=target_rate, cookie=cookie, 
      token=token, ear_passphrase=ear_passphrase, upload=False, persistent=persistent, args=args, old=old)
    

signal.signal(signal.SIGINT, ctrlc_handler)
mgmt_thread = Thread( target=start_mgmt )
mgmt_thread.daemon = True
mgmt_thread.start()


