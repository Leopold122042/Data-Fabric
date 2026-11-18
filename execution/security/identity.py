"""Replaceable B identity boundary. Local demo identities are never production authentication."""
import ipaddress
import os

from fastapi import Request
from platform_core.storage import DomainError

DEMO_USERS={'platform':{'id':'demo-platform','role':'platform'},'provider':{'id':'demo-provider','role':'provider'},
            'requester':{'id':'demo-requester','role':'requester'},'auditor':{'id':'demo-auditor','role':'auditor'}}

class LocalDemoIdentity:
    def principal(self,request:Request):
        lan_demo = False
        client_host = request.client.host if request.client else None
        if client_host not in (None, '127.0.0.1', '::1', 'testclient'):
            try:
                remote_ip = ipaddress.ip_address(client_host)
            except ValueError:
                raise DomainError('无法验证演示访问来源', 403)
            allowed_networks = os.environ.get('MEDFABRIC_DEMO_TRUSTED_NETWORKS', '').strip()
            if allowed_networks:
                try:
                    networks = [ipaddress.ip_network(value.strip(), strict=False)
                                for value in allowed_networks.split(',') if value.strip()]
                except ValueError:
                    raise DomainError('演示访问白名单配置无效', 403)
                network_allowed = any(remote_ip.version == network.version and remote_ip in network
                                      for network in networks)
            else:
                network_allowed = remote_ip.is_private
            if os.environ.get('MEDFABRIC_DEMO_ALLOW_PRIVATE_NETWORK') != '1' or not network_allowed:
                raise DomainError('演示访问来源不在本机或校园网白名单内',403)
            lan_demo = True
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            raise DomainError('不接受其他站点发起的管理请求',403)
        # LAN demo devices see the same platform workspace by default.
        # Dataset authorization and execution checks remain in the data plane.
        name='platform' if lan_demo else request.headers.get('x-demo-role','platform')
        if name not in DEMO_USERS:raise DomainError('未知演示身份',403)
        return dict(DEMO_USERS[name],demo=True)
