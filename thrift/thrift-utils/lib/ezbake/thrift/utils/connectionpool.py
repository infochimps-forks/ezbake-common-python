#   Copyright (C) 2013-2014 Computer Sciences Corporation
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

# -*- coding: utf-8 -*-
"""
Created on Mon Apr 14 11:13:45 2014

@author: jhastings
"""

import threading
import Queue
import inspect
from random import shuffle

from thrift.transport import TSocket
from thrift.transport import TTransport
from thrift.protocol import TBinaryProtocol

from ..transport.EzSSLSocket import TSSLSocket

import logging
logger = logging.getLogger(__name__)

from evictqueue import LifoQueueWithEvict, current_time_millis


class ThriftConnectionPool(object):
    """
    Thrift Conncetion pool

    Attributes:
        endpoints: list of endpoints that thrift server is listening on
        client_cls: thrift client class to instantiate
        size: optional size of the pool, equivalent to max number of
            connections. Defaults to ThriftConnectionPool.DEFAULT_SIZE
        ttransport: optional thrift transport to use, defaults to
            TTransport.TBufferedTransport
        tprotocol: optional thrift protocol to use, defaults to
            TBinaryProtocol.TBinaryProtocol
        ca_certs, cert, key: files needed by SSL
    """
    DEFAULT_SIZE = 5

    def __init__(self, endpoints, client_cls, size=DEFAULT_SIZE,
                 ttransport=TTransport.TBufferedTransport,
                 tprotocol=TBinaryProtocol.TBinaryProtocol,
                 use_ssl=False, ca_certs=None, cert=None, key=None):
        """
        Initialize the thrift connection pool for a specific server and client
        """
        self.endpoints = endpoints
        self.client_cls = client_cls
        self.ttransport = ttransport
        self.tprotocol = tprotocol

        self.use_ssl = use_ssl
        self.ca_certs = ca_certs
        self.cert = cert
        self.key = key

        self._closed = False
        self.size = size
        self._semaphore = threading.BoundedSemaphore(size)
        self._connection_queue = LifoQueueWithEvict(size)

    def evict_check(self, idle_threshold_millis):
        self._connection_queue.evict(idle_threshold_millis, self._close_connection)

    def close(self):
        """
        Closes the connection pool. Outstanding connections will be closed on
        the next call to return_connection
        """
        self._closed = True
        while not self._connection_queue.empty():
            try:
                (conn, t) = self._connection_queue.get(block=False)
                self._connection_queue.task_done()
                try:
                    self._close_connection(conn)
                except:
                    # ignore thrift errors, nothing we can do here
                    pass
            except Queue.Empty:
                # ignore empty queue - that's what we want anyway
                pass

    def _create_connection(self):
        """
        Helper that creates a thrift client connection to the instance host
        and port.

        Returns:
            An instance of the thrift client class with an open transport
        """

        endpoints = self.endpoints[:]
        shuffle(endpoints)
        for endpoint in endpoints:
            try:
                host, port = endpoint.split(':')
                logger.debug('connecting to %s:%s. ssl? %s', host, port, self.use_ssl)
                if self.use_ssl:
                    socket = TSSLSocket(host=host, port=int(port),
                                        ca_certs=self.ca_certs, cert=self.cert, key=self.key)
                else:
                    socket = TSocket.TSocket(host, int(port))

                transport = self.ttransport(socket)
                protocol = self.tprotocol(transport)
                connection = self.client_cls(protocol)
                transport.open()
            except Exception as e:
                logger.info("connection to %s failed, try other endpoints: %s", endpoint, e)
                continue
            return connection

        raise Exception("Can't open a connection on any endpoints!")

    @staticmethod
    def _close_connection(conn):
        """
        Helper that closes a thrift client input and output transports

        Args:
            conn: the thrift connection that should be closed
        """
        try:
            conn._iprot.trans.close()
        except:
            logger.warn('failed to close iprot transport on %s', conn)
        try:
            conn._oprot.trans.close()
        except:
            logger.warn('failed to close oprot transport on %s', conn)

    def get_connection(self):
        """
        Get a connection from the pool. If the pool is empty, it will create
        a new one. If the pool is full, it blocks until one is available.

        Returns:
            A connected thrift client
        """
        self._semaphore.acquire()
        if self._closed:
            raise RuntimeError('connection pool already closed')
        try:
            (conn, t) = self._connection_queue.get(block=False)
            self._connection_queue.task_done()
            return conn
        except Queue.Empty:
            try:
                return self._create_connection()
            except:
                self._semaphore.release()
                raise

    def return_connection(self, conn):
        """
        Return the given connection to the pool of available connections. If
        the pool is closed, just close the connection.

        Args:
            conn: the thrift connection to be returned to the connection pool
        """
        if self._closed:
            self._close_connection(conn)
        else:
            self._connection_queue.put((conn, current_time_millis()))
            self._semaphore.release()

    def release_conn(self, conn):
        """
        Release the connection without returning to the pool. Use when the
        connection is bad, i.e. catching a TTransportException.

        Args:
            conn: the thrift connection being released
        """
        try:
            self._close_connection(conn)
        except:
            # Ignore, not returning to the pool anyway
            pass
        if not self._closed:
            self._semaphore.release()


class PoolingThriftClient(object):
    """
    Wrapper for thrift clients that handles connection pooling. This is done
    by injecting all methods from the client interface into the PoolingThriftClient,
    and wrapping the internal calls in a pool.get/pool.return call.

    Attributes:
        endpoints: list of endpoints that thrift server is listening on
        client_cls: the thrift client class this instance will contain
        pool_size: optional integer max number of concurrent connections to
          keep in the pool
        retries: integer number of times to retry if clients returned from the
          pool are throwing TTransportExceptions
        ttransport: thrift transport class passed down to the connection pool
        tprotocol: thrift protocol class passed down to the connection pool
        ca_certs, cert, key: files needed by SSL
    """
    def __init__(self, endpoints, client_cls,
                 pool_size=ThriftConnectionPool.DEFAULT_SIZE, retries=3,
                 ttransport=TTransport.TBufferedTransport,
                 tprotocol=TBinaryProtocol.TBinaryProtocol,
                 use_ssl=False, ca_certs=None, cert=None, key=None):
        """Initialize the connection pool and inject thrift client methods"""
        self.login_token = None
        self.retries = retries
        self._pool = ThriftConnectionPool(endpoints, client_cls, pool_size,
                                          ttransport, tprotocol, use_ssl, ca_certs, cert, key)
        #inject all methods defined in the thrift client Iface
        for m in inspect.getmembers(client_cls, predicate=inspect.ismethod):
            setattr(self, m[0], self.__create_proxy(m[0]))

    def close(self):
        """Close the underlying connection pool"""
        self._pool.close()

    def _process_thrift_args(self, client, method, args):
        """
        Hook that allows subclasses to do some processing of the client and
        it's args before the final thrift method is called

        Args:
            client: a connected thrift client
            method: thrift method being called on the client
            args: args being passed to the thrift method

        Returns:
            The final arguments to be passed to the method call
        """
        return args

    def __create_proxy(self, method_name):
        """Create a wrapper around a thrift method call"""
        def __proxy_method(*args):
            return self.__call_thrift_method(method_name, *args)
        return __proxy_method

    def __call_thrift_method(self, method, *args):

        """Make a thrift method call"""
        attempts_left = self.retries
        result = None
        while True:
            conn = self._pool.get_connection()
            try:
                args = self._process_thrift_args(conn, method, args)
                result = getattr(conn, method)(*args)
            except TTransport.TTransportException as e:
                #broken connection, release it
                self._pool.release_conn(conn)
                if attempts_left > 0:
                    attempts_left -= 1
                    continue
                raise e
            except Exception as e:
                #data exceptions, return connection and don't retry
                self._pool.return_connection(conn)
                raise

            # call completed successfully, return connection to pool
            self._pool.return_connection(conn)
            return result
