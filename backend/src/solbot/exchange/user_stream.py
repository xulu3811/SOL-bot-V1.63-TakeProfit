"""Current WS API signature subscription and loss-aware event normalization."""
import hashlib
import hmac
from .binance_private import AuthorizationDenied, Credentials, MockTransport, HTTPTransport
from solbot.mathematics.decimal_utils import D

BOT_ORDER_PREFIXES = ('sb5-', 'p5p-', 'p5x-', 'cbotby', 'cbotsl', 'cbotex', 'cbot2s', 'cbot2x')


class UserStream:
    def __init__(self,client): self.client=client; self.subscription_id=None; self.connected=False
    def subscribe_request(self,request_id):
        self.client._authorize('READ')
        if self.client.synced_at is None or not 0<=self.client.clock()-self.client.synced_at<=60000: raise AuthorizationDenied('clock synchronization required')
        try: cred=self.client.loader()
        except Exception: raise AuthorizationDenied('credential load failed') from None
        if not isinstance(cred,Credentials): raise AuthorizationDenied('invalid credentials')
        try:
            params={'apiKey':cred.api_key,'timestamp':self.client.clock()+self.client.offset,'recvWindow':self.client.recv_window}
            payload='&'.join(f'{key}={value}' for key,value in sorted(params.items()))
            params['signature']=hmac.new(cred.secret.encode(),payload.encode(),hashlib.sha256).hexdigest()
            self.client._authorize('READ')
            return {'id':request_id,'method':'userDataStream.subscribe.signature','params':params}
        finally: cred=None
    def acknowledge(self,response):
        self.client._authorize('READ')
        if response.get('status')!=200 or type(response.get('result',{}).get('subscriptionId')) is not int: raise ValueError('subscription not accepted')
        self.subscription_id=response['result']['subscriptionId']; self.connected=True
    def receive(self,message):
        self.client._authorize('READ')
        if not self.connected or message.get('subscriptionId')!=self.subscription_id: raise ValueError('unbound stream')
        event=message.get('event',{}); typ=event.get('e')
        if typ in ('eventStreamTerminated','serverShutdown'):
            self.connected=False; return {'kind':'RECONCILE_REQUIRED','reason':typ}
        if typ=='executionReport':
            symbol=event['s']
            robot=symbol in ('SUIUSDT','BNBUSDT') or (symbol=='SOLUSDT' and (str(event.get('c','')).startswith(BOT_ORDER_PREFIXES) or str(event.get('C','')).startswith(BOT_ORDER_PREFIXES)))
            if not robot: return {'kind':'MANUAL_ORDER','symbol':symbol,'client_order_id':event.get('c'),'status':event.get('X'),'raw':event}
            return {'kind':'ORDER','symbol':symbol,'client_order_id':event['c'],'order_id':event['i'],'status':event['X'],'execution_type':event['x'],'trade_id':event.get('t',-1),'event_time':event['E'],'transaction_time':event['T'],'quantity':str(D(event['q'],nonnegative=True)),'cumulative_quantity':str(D(event['z'],nonnegative=True)),'cumulative_quote':str(D(event['Z'],nonnegative=True)),'last_quantity':str(D(event['l'],nonnegative=True)),'last_price':str(D(event['L'],nonnegative=True)),'commission':str(D(event.get('n','0'),nonnegative=True)),'commission_asset':event.get('N'),'raw':event}
        if typ=='outboundAccountPosition': return {'kind':'BALANCE_PARTIAL','observed_at':event['u'],'balances':{x['a']:{'free':str(D(x['f'],nonnegative=True)),'locked':str(D(x['l'],nonnegative=True))} for x in event['B']}}
        if typ in ('balanceUpdate','externalLockUpdate'): return {'kind':'RECONCILE_REQUIRED','reason':typ,'raw':event}
        return {'kind':'UNRECOGNIZED_EVENT','raw':event}
    def disconnected(self):
        self.connected=False; return {'kind':'RECONCILE_REQUIRED','reason':'STREAM_DISCONNECTED'}
    def close(self):
        # No unsubscribe request after OFF: close the transport without API action.
        self.connected=False; self.client.close()

    def _bind_socket_close(self,socket,loop):
        """Session revocation actively closes an idle socket, without another read."""
        import asyncio
        def revoke():
            self.connected=False
            if not loop.is_closed():
                loop.call_soon_threadsafe(lambda: asyncio.create_task(socket.close()))
        if not hasattr(self.client.gate,'register_close'):
            raise AuthorizationDenied('active revocation callback required')
        self.client.gate.register_close(revoke)

    async def production_events(self):
        """Network loop ready for future accepted release; P5 cannot connect."""
        self.client._authorize('READ')
        HTTPTransport._require_accepted_release()
        import json
        import asyncio
        import uuid
        import websockets
        try:
            async with websockets.connect('wss://ws-api.binance.com:443/ws-api/v3',open_timeout=10,close_timeout=3,ping_interval=20,ping_timeout=20,max_size=1_000_000) as socket:
                self._bind_socket_close(socket,asyncio.get_running_loop())
                request=self.subscribe_request(str(uuid.uuid4()))
                await socket.send(json.dumps(request))
                # Only the matching subscribe ACK may establish stream ownership.
                response=json.loads(await socket.recv())
                if response.get('id')!=request['id']: raise ValueError('unexpected subscription response')
                self.acknowledge(response)
                request=None
                async for raw in socket:
                    self.client._authorize('READ')
                    yield self.receive(json.loads(raw))
        finally:
            self.disconnected()
