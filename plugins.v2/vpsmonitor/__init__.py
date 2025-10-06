"""
VPS 限速监控插件（plugins.v2）

功能：
- 通过 SCP SOAP 接口获取 VPS 列表与详情
- 检测是否存在网卡 trafficThrottled=True（被限速）
- 通过 MoviePilot 通知事件发送汇总结果

依赖：requests、zeep

安全说明：提供可选“不安全 TLS”模式，关闭证书校验以兼容老旧服务端。默认关闭，仅在必要时短期启用。
"""

from typing import Any, Dict, List, Optional, Tuple

from app.core.event import Event, eventmanager
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType


class VPSMonitor(_PluginBase):
    # 基本信息
    plugin_name = "Netcup VPS 限速监控"
    plugin_desc = "定时检测NC SCP 下 VPS 是否被限速，并通过通知插件发送结果。"
    plugin_icon = "https://raw.githubusercontent.com/YunFeng86/MoviePilot-Plugins/main/icons/Netcup_A.png"
    plugin_version = "0.3.0"
    plugin_author = "YunFeng"
    author_url = "https://github.com/YunFeng86"
    plugin_config_prefix = "vpsmonitor_"
    plugin_order = 90
    auth_level = 1

    # 配置项
    _enabled: bool = False
    _cron: Optional[str] = None
    _onlyonce: bool = False
    _wsdl_url: str = "https://www.servercontrolpanel.de/WSEndUser?wsdl"
    _customer: Optional[str] = None
    _password: Optional[str] = None
    _language: str = "en"
    _notify_all_ok: bool = True
    _insecure_tls: bool = False
    _debug_dump: bool = False
    # REST 配置
    _api_mode: str = "rest"            # rest/soap
    _rest_base_url: Optional[str] = None
    _rest_access_token: Optional[str] = None   # Bearer Access Token（存储）
    _rest_refresh_token: Optional[str] = None  # Refresh Token（存储）
    _rest_token_expires_at: Optional[int] = None  # 过期时间戳（秒）
    # 多账户（每条：id/name/enabled/api_mode/rest_* 或 customer/password）
    _accounts: List[Dict[str, Any]] = []

    def init_plugin(self, config: Optional[dict] = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = (config.get("cron") or "").strip() or None
            self._onlyonce = config.get("onlyonce", False)
            # WSDL 地址固定为默认值，不从配置覆盖
            self._wsdl_url = self._wsdl_url
            self._customer = config.get("customer")
            self._password = config.get("password")
            # 接口语言写死为 en（不暴露到配置）
            self._language = "en"
            self._notify_all_ok = bool(config.get("notify_all_ok", True))
            self._insecure_tls = bool(config.get("insecure_tls", False))
            self._debug_dump = bool(config.get("debug_dump", False))

            # REST 相关
            self._api_mode = (config.get("api_mode") or "rest").strip() or "rest"
            if self._api_mode not in ("rest", "soap"):
                self._api_mode = "rest"
            self._rest_base_url = (config.get("rest_base_url") or "").strip() or None
            self._rest_access_token = (config.get("rest_access_token") or "").strip() or None
            self._rest_refresh_token = (config.get("rest_refresh_token") or "").strip() or None
            self._rest_token_expires_at = config.get("rest_token_expires_at")
            # 多账户
            accs = config.get("accounts")
            if isinstance(accs, list):
                self._accounts = accs

            # 保存配置（清理 onlyonce）
            if self._onlyonce:
                # 立即运行一次
                try:
                    self._run_check()
                except Exception as e:
                    logger.error(f"VPS 限速监控立即运行失败：{e}")
                finally:
                    self._onlyonce = False
            self.__update_config()

    def get_state(self) -> bool:
        return bool(self._enabled)

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "wsdl_url": self._wsdl_url,
            "customer": self._customer,
            "password": self._password,
            "language": self._language,
            "notify_all_ok": self._notify_all_ok,
            "insecure_tls": self._insecure_tls,
            "debug_dump": self._debug_dump,
            # REST
            "api_mode": self._api_mode,
            "rest_base_url": self._rest_base_url,
            "rest_access_token": self._rest_access_token,
            "rest_refresh_token": self._rest_refresh_token,
            "rest_token_expires_at": self._rest_token_expires_at,
            # 多账户
            "accounts": self._accounts,
        })

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/start_device_flow",
                "endpoint": self.start_device_flow,
                "methods": ["POST"],
                "summary": "生成设备码并返回验证链接",
                "description": "调用 SCP OpenID 设备码接口，返回 verification_uri_complete 与 user_code"
            },
            {
                "path": "/poll_device_token",
                "endpoint": self.poll_device_token,
                "methods": ["POST"],
                "summary": "根据 device_code 轮询获取访问令牌",
                "description": "授权后获取 access_token/refresh_token 并保存"
            },
            {
                "path": "/revoke_device_token",
                "endpoint": self.revoke_device_token,
                "methods": ["POST"],
                "summary": "撤销刷新令牌并清除授权",
                "description": "调用 revoke 接口，清空本地令牌"
            },
            {
                "path": "/account_add",
                "endpoint": self.account_add,
                "methods": ["POST"],
                "summary": "新增账户",
                "description": "新增一个待授权账户（name）"
            },
            {
                "path": "/account_remove",
                "endpoint": self.account_remove,
                "methods": ["POST"],
                "summary": "删除账户",
                "description": "根据 id 删除账户"
            },
            {
                "path": "/account_update",
                "endpoint": self.account_update,
                "methods": ["POST"],
                "summary": "更新账户",
                "description": "更新账户名称/启用/模式/凭据等"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        定时任务
        - 使用 Cron 表达式（5 字段）
        """
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger  # type: ignore
            try:
                trigger = CronTrigger.from_crontab(self._cron)
                return [{
                    "id": "VPSMonitorService",
                    "name": "VPS 限速监控",
                    "trigger": trigger,
                    "func": self._run_check,
                    "kwargs": {}
                }]
            except Exception as e:
                logger.error(f"VPS 限速监控 Cron 配置错误：{e}")
        return []

    def stop_service(self):
        """退出插件"""
        pass

    def get_page(self) -> Optional[List[dict]]:
        """拼装插件详情页面，展示当前配置与状态"""
        enabled_status = "已启用" if self.get_state() else "未启用"
        status_color = "success" if self.get_state() else "error"

        cron_text = self._cron or "未配置"
        wsdl_text = self._wsdl_url or "未配置"
        language_text = self._language or "未配置"
        insecure_text = "已开启" if self._insecure_tls else "未开启"
        notify_ok_text = "已开启" if self._notify_all_ok else "未开启"
        return [
            {
                'component': 'div',
                'props': {'class': 'pa-4'},
                'content': [
                    {
                        'component': 'VCard',
                        'props': {'class': 'mb-4'},
                        'content': [
                            {'component': 'VCardTitle', 'props': {'class': 'text-h5'}, 'text': 'VPS 限速监控状态'},
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VList',
                                        'props': {'dense': True},
                                        'content': [
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {
                                                        'component': 'VListItemTitle',
                                                        'content': [
                                                            {'component': 'strong', 'text': '状态：'},
                                                            {
                                                                'component': 'VChip',
                                                                'props': {'color': status_color, 'small': True, 'class': 'ml-1'},
                                                                'text': enabled_status
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {'component': 'VListItemTitle', 'content': [
                                                        {'component': 'strong', 'text': 'Cron：'},
                                                        {'component': 'span', 'text': cron_text}
                                                    ]}
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        import json as _json
        js_api_token = _json.dumps(settings.API_TOKEN)

        return [
            {
                'component': 'VForm',
                'content': [
                    
                    
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enabled',
                                        'label': '启用插件',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'onlyonce',
                                        'label': '立即运行一次',
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSelect',
                                    'props': {
                                        'model': 'api_mode',
                                        'label': 'API 模式',
                                        'items': [
                                            {'title': 'REST', 'value': 'rest'},
                                            {'title': 'WSDL', 'value': 'soap'}
                                        ],
                                        'clearable': False
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VCronField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '执行周期',
                                        'placeholder': '5位cron表达式，留空不执行'
                                    }
                                }]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'notify_all_ok',
                                            'label': '通知策略',
                                            'items': [
                                                {'title': '仅发送报错时通知', 'value': False},
                                                {'title': '即使正常也通知', 'value': True}
                                            ],
                                            'clearable': False
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'customer',
                                        'label': 'SCP 客户号',
                                        'show': "{{ api_mode == 'soap' }}"
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'password',
                                        'label': 'SCP 密码',
                                        'show': "{{ api_mode == 'soap' }}"
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # REST 授权提示移入“账户管理”后续页面（此处暂不展示按钮）
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'insecure_tls',
                                        'label': '跳过证书校验（不安全 TLS）',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'debug_dump',
                                        'label': '调试模式（输出完整返回）',
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": self._enabled,
            "cron": self._cron or "",
            "onlyonce": False,
            "wsdl_url": self._wsdl_url,
            "customer": self._customer or "",
            "password": self._password or "",
            # 语言固定为 en
            "language": "en",
            "notify_all_ok": self._notify_all_ok,
            "insecure_tls": self._insecure_tls,
            "debug_dump": self._debug_dump,
            # REST 默认
            "api_mode": self._api_mode or "rest",
            "rest_base_url": "",
            "rest_access_token": self._rest_access_token or "",
            "acct_expanded": None,
        }

    # ============ 内部实现 ============
    def _refresh_access_token(self) -> bool:
        """使用 Refresh Token 刷新 Access Token，并持久化。
        返回：True 刷新成功/False 失败或无可用刷新令牌。
        """
        try:
            import time
            import requests
            if not self._rest_refresh_token:
                return False
            resp = requests.post(
                'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/token',
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': self._rest_refresh_token,
                    'client_id': 'scp'
                }, timeout=15
            )
            if resp.status_code != 200:
                logger.warning(f"REST 刷新令牌失败：{resp.text}")
                return False
            data = resp.json() or {}
            self._rest_access_token = data.get('access_token')
            self._rest_refresh_token = data.get('refresh_token') or self._rest_refresh_token
            expires_in = data.get('expires_in') or 300
            self._rest_token_expires_at = int(time.time()) + int(expires_in)
            self.__update_config()
            return True
        except Exception as e:
            logger.warning(f"REST 刷新令牌异常：{e}")
            return False
    def _run_check(self):
        """执行一次检测（支持多账户聚合）"""
        # 依赖检查
        try:
            import requests
            import ssl
            from requests import Session
            from requests.adapters import HTTPAdapter
            from urllib3.poolmanager import PoolManager
            from zeep import Client, Settings  # type: ignore
            from zeep.transports import Transport  # type: ignore
        except Exception as e:
            logger.error(f"VPS 限速监控缺少依赖：{e}")
            self._notify("🔴 VPS 监控错误", f"缺少依赖：{e}", success=False)
            return

        # ========== 多账户优先 ==========
        # 若配置了 accounts 且存在启用的账户，则逐账户执行并汇总；
        # 否则回退到单账户（全局配置）逻辑以保持兼容。
        try_accounts = []
        if isinstance(self._accounts, list):
            try_accounts = [a for a in self._accounts if a and a.get('enabled', True)]

        if try_accounts:
            try:
                import requests, time as _t, ssl
                from requests import Session
                from requests.adapters import HTTPAdapter
                from urllib3.poolmanager import PoolManager

                base = (self._rest_base_url or 'https://www.servercontrolpanel.de/scp-core')

                # 聚合结果
                throttled_map: Dict[str, List[str]] = {}
                ok_counts: List[Tuple[str, int]] = []
                warns: List[str] = []

                def _refresh_for_account(acc: Dict[str, Any]) -> bool:
                    """使用账户内 refresh_token 刷新 access_token"""
                    try:
                        import requests, time
                        rt = acc.get('rest_refresh_token')
                        if not rt:
                            return False
                        resp = requests.post(
                            'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/token',
                            data={
                                'grant_type': 'refresh_token',
                                'refresh_token': rt,
                                'client_id': 'scp'
                            }, timeout=15
                        )
                        if resp.status_code != 200:
                            logger.warning(f"REST 刷新令牌失败（账户 {acc.get('name','')}）：{resp.text}")
                            return False
                        data = resp.json() or {}
                        acc['rest_access_token'] = data.get('access_token')
                        acc['rest_refresh_token'] = data.get('refresh_token') or rt
                        expires_in = data.get('expires_in') or 300
                        acc['rest_token_expires_at'] = int(time.time()) + int(expires_in)
                        self.__update_config()
                        return True
                    except Exception as e:
                        logger.warning(f"REST 刷新令牌异常（账户 {acc.get('name','')}）：{e}")
                        return False

                for acc in try_accounts:
                    name = str(acc.get('name') or acc.get('id') or '未命名账户')
                    mode = (acc.get('api_mode') or 'rest').lower()
                    if mode == 'rest':
                        s = requests.Session()
                        s.verify = not self._insecure_tls

                        # 访问令牌就绪性：无 AT 但有 RT → 刷新；即将过期 → 刷新
                        now = int(_t.time())
                        at = acc.get('rest_access_token')
                        rt = acc.get('rest_refresh_token')
                        exp = acc.get('rest_token_expires_at')
                        if (not at) and rt:
                            _refresh_for_account(acc)
                            at = acc.get('rest_access_token')
                            exp = acc.get('rest_token_expires_at')
                        elif exp and now >= int(exp) - 60:
                            _refresh_for_account(acc)
                            at = acc.get('rest_access_token')
                            exp = acc.get('rest_token_expires_at')

                        if not at:
                            warns.append(f"[{name}] 未授权 REST（缺少 Access Token）")
                            continue

                        headers = {'Authorization': f"Bearer {at}"}
                        try:
                            r = s.get(f"{base}/api/v1/servers", headers=headers, timeout=15)
                            if r.status_code == 401 and _refresh_for_account(acc):
                                headers = {'Authorization': f"Bearer {acc.get('rest_access_token')}"}
                                r = s.get(f"{base}/api/v1/servers", headers=headers, timeout=15)
                            r.raise_for_status()
                            servers = r.json()
                            if isinstance(servers, dict) and 'servers' in servers:
                                servers = servers.get('servers')
                            if not isinstance(servers, list):
                                raise Exception("REST 返回格式异常：servers 不是列表")

                            def first_ipv4(v):
                                ips = v.get('ips') if isinstance(v.get('ips'), list) else []
                                for ip in ips:
                                    if ':' not in ip:
                                        return ip
                                return ips[0] if ips else '未知'

                            throttled_list: List[str] = []
                            for sv in servers:
                                sid = sv.get('id') or sv.get('serverId') or sv.get('uuid') or sv.get('vServerName')
                                sname = sv.get('vServerName') or sv.get('hostname') or sid
                                if not sid:
                                    continue
                                r2 = s.get(f"{base}/api/v1/servers/{sid}/interfaces", headers=headers, timeout=15)
                                if r2.status_code == 401 and _refresh_for_account(acc):
                                    headers = {'Authorization': f"Bearer {acc.get('rest_access_token')}"}
                                    r2 = s.get(f"{base}/api/v1/servers/{sid}/interfaces", headers=headers, timeout=15)
                                r2.raise_for_status()
                                itf_json = r2.json()
                                if isinstance(itf_json, dict) and 'interfaces' in itf_json:
                                    interfaces = itf_json.get('interfaces') or []
                                else:
                                    interfaces = itf_json if isinstance(itf_json, list) else []
                                is_throttled = False
                                primary_ip = first_ipv4(sv)
                                for iface in interfaces:
                                    if iface.get('trafficThrottled') is True:
                                        is_throttled = True
                                        ipv4s = iface.get('ipv4IP') or []
                                        if isinstance(ipv4s, list) and ipv4s:
                                            primary_ip = ipv4s[0]
                                        break
                                if is_throttled:
                                    throttled_list.append(f"• {sname} ({primary_ip})")

                            throttled_map[name] = throttled_list
                            ok_counts.append((name, len(servers)))
                        except Exception as e:
                            warns.append(f"[{name}] REST 调用失败：{e}")

                    else:  # SOAP per-account
                        # 账户 SOAP 凭据
                        customer = acc.get('customer')
                        password = acc.get('password')
                        if not customer or not password:
                            warns.append(f"[{name}] SOAP 模式缺少凭据")
                            continue

                        class TLSAdapter(HTTPAdapter):
                            def __init__(self, insecure: bool = False, *args, **kwargs):
                                self._insecure = insecure
                                super().__init__(*args, **kwargs)
                            def init_poolmanager(self, connections, maxsize, block=False):
                                ctx = ssl.create_default_context()
                                try:
                                    ctx.set_ciphers('DEFAULT@SECLEVEL=1')
                                except Exception:
                                    pass
                                if self._insecure:
                                    ctx.check_hostname = False
                                    ctx.verify_mode = ssl.CERT_NONE
                                self.poolmanager = PoolManager(
                                    num_pools=connections,
                                    maxsize=maxsize,
                                    block=block,
                                    ssl_context=ctx)

                        session = Session()
                        session.mount('https://', TLSAdapter(insecure=self._insecure_tls))
                        session.verify = not self._insecure_tls

                        from zeep import Client, Settings  # type: ignore
                        from zeep.transports import Transport  # type: ignore
                        settings = Settings(strict=False, xml_huge_tree=True)
                        try:
                            client = Client(wsdl=self._wsdl_url, settings=settings, transport=Transport(session=session))
                        except Exception as e:
                            warns.append(f"[{name}] 连接 WSDL 失败：{e}")
                            continue

                        try:
                            vps_list = client.service.getVServers(loginName=customer, password=password)
                            if not vps_list:
                                ok_counts.append((name, 0))
                                throttled_map[name] = []
                                continue
                        except Exception as e:
                            warns.append(f"[{name}] 获取 VPS 列表失败：{e}")
                            continue

                        throttled_list: List[str] = []
                        for vname in vps_list:
                            try:
                                info = client.service.getVServerInformation(
                                    loginName=customer,
                                    password=password,
                                    vservername=vname,
                                    language=self._language
                                )
                                ips = getattr(info, 'ips', []) or []
                                primary_ip = ips[0] if ips else '未知'
                                interfaces = getattr(info, 'serverInterfaces', []) or []
                                is_throttled = False
                                for iface in interfaces:
                                    if hasattr(iface, 'trafficThrottled') and getattr(iface, 'trafficThrottled', False) is True:
                                        is_throttled = True
                                        break
                                if is_throttled:
                                    throttled_list.append(f"• {vname} ({primary_ip})")
                            except Exception as e:
                                logger.warning(f"[{name}] 获取 {vname} 信息失败：{e}")
                        throttled_map[name] = throttled_list
                        ok_counts.append((name, len(vps_list)))

                # 汇总通知
                any_throttled = any(v for v in throttled_map.values())
                if any_throttled:
                    lines: List[str] = []
                    for n, lst in throttled_map.items():
                        if not lst:
                            continue
                        lines.append(f"【{n}】")
                        lines.extend(lst)
                    if warns:
                        lines.append("")
                        lines.append("注意：")
                        lines.extend([f"- {w}" for w in warns])
                    self._notify("⚠️ VPS 被限速（多账户）", "\n".join(lines), success=False)
                else:
                    if self._notify_all_ok:
                        lines = [f"• {n}：共 {cnt} 台，均未被限速" for n, cnt in ok_counts]
                        if warns:
                            lines.append("")
                            lines.append("注意：")
                            lines.extend([f"- {w}" for w in warns])
                        self._notify("🟢 所有账户 VPS 正常", "\n".join(lines), success=True)
                return
            except Exception as e:
                logger.error(f"多账户聚合执行失败：{e}")
                # 不中断，继续尝试单账户逻辑作为降级

        # ========== 尝试 REST 模式（单账户兼容） ==========
        if self._api_mode == "rest":
            try:
                throttled_rest: List[str] = []
                base = 'https://www.servercontrolpanel.de/scp-core'
                if not base:
                    raise Exception("未配置 REST 基址")

                s = requests.Session()
                s.verify = not self._insecure_tls
                headers = {}
                auth = None
                # Token 预处理：若无 Access Token 但有 Refresh Token，则尝试刷新；若过期也刷新
                import time as _t
                now = int(_t.time())
                if (not self._rest_access_token) and self._rest_refresh_token:
                    self._refresh_access_token()
                elif self._rest_token_expires_at and now >= int(self._rest_token_expires_at) - 60:
                    # 过期前60秒尝试刷新
                    self._refresh_access_token()
                if not self._rest_access_token:
                    # 无法自动获取令牌，给出温和提示并退出
                    logger.warning("REST 调用跳过：未配置 REST Access Token (Bearer)，请在插件页完成授权")
                    self._notify("🔴 REST 未授权",
                                 "未检测到 Access Token，请在插件配置页点击‘获取验证链接’完成授权。",
                                 success=False)
                    return
                headers['Authorization'] = f"Bearer {self._rest_access_token}"

                r = s.get(f"{base}/api/v1/servers", headers=headers, timeout=15)
                r.raise_for_status()
                servers = r.json()
                if isinstance(servers, dict) and 'servers' in servers:
                    servers = servers.get('servers')
                if not isinstance(servers, list):
                    raise Exception("REST 返回格式异常：servers 不是列表")

                def first_ipv4(v):
                    ips = v.get('ips') if isinstance(v.get('ips'), list) else []
                    for ip in ips:
                        if ':' not in ip:
                            return ip
                    return ips[0] if ips else '未知'

                for sv in servers:
                    sid = sv.get('id') or sv.get('serverId') or sv.get('uuid') or sv.get('vServerName')
                    name = sv.get('vServerName') or sv.get('hostname') or sid
                    if not sid:
                        continue
                    r2 = s.get(f"{base}/api/v1/servers/{sid}/interfaces", headers=headers, timeout=15)
                    r2.raise_for_status()
                    itf_json = r2.json()
                    if isinstance(itf_json, dict) and 'interfaces' in itf_json:
                        interfaces = itf_json.get('interfaces') or []
                    else:
                        interfaces = itf_json if isinstance(itf_json, list) else []
                    is_throttled = False
                    primary_ip = first_ipv4(sv)
                    for iface in interfaces:
                        if iface.get('trafficThrottled') is True:
                            is_throttled = True
                            ipv4s = iface.get('ipv4IP') or []
                            if isinstance(ipv4s, list) and ipv4s:
                                primary_ip = ipv4s[0]
                            break
                    if is_throttled:
                        throttled_rest.append(f"• {name} ({primary_ip})")

                if throttled_rest:
                    self._notify("⚠️ VPS 被限速", "以下 VPS 当前被限速：\n" + "\n".join(throttled_rest), success=False)
                else:
                    if self._notify_all_ok:
                        self._notify("🟢 所有 VPS 正常", f"共 {len(servers)} 台 VPS，均未被限速。", success=True)
                return
            except Exception as e:
                # 若遇 401 再尝试刷新一次后重试一次服务器列表
                try:
                    msg = str(e)
                    if '401' in msg or 'Unauthorized' in msg:
                        if self._refresh_access_token():
                            s = requests.Session()
                            s.verify = not self._insecure_tls
                            headers = {'Authorization': f"Bearer {self._rest_access_token}"}
                            r = s.get(f"{(self._rest_base_url or 'https://www.servercontrolpanel.de/scp-core')}/api/v1/servers", headers=headers, timeout=15)
                            r.raise_for_status()
                            logger.info("REST 401 后刷新令牌重试成功")
                            return
                except Exception as _e:
                    logger.warning(f"REST 刷新重试失败：{_e}")
                logger.error(f"REST 调用失败：{e}")
                self._notify("🔴 REST 调用失败", str(e), success=False)
                return

        # ========== SOAP 模式（单账户兼容） ==========
        if self._api_mode == "soap":
            # SOAP 需要凭据
            if not self._customer or not self._password:
                logger.warning("VPS 监控未配置 SCP 凭据（SOAP 模式）")
                self._notify("🔴 VPS 监控未配置", "请填写 SCP 客户号与密码（SOAP）", success=False)
                return

            # 自定义 TLS 适配器（可选）
            insecure_flag = self._insecure_tls
            class TLSAdapter(HTTPAdapter):
                def __init__(self, insecure: bool = False, *args, **kwargs):
                    # 注意顺序：先设置属性，再调用父类 __init__，因其会调用 init_poolmanager
                    self._insecure = insecure
                    super().__init__(*args, **kwargs)

                def init_poolmanager(self, connections, maxsize, block=False):
                    ctx = ssl.create_default_context()
                    try:
                        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
                    except Exception:
                        pass
                    if self._insecure:
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                    self.poolmanager = PoolManager(
                        num_pools=connections,
                        maxsize=maxsize,
                        block=block,
                        ssl_context=ctx)

            # 构建会话
            session = Session()
            session.mount('https://', TLSAdapter(insecure=insecure_flag))
            session.verify = not insecure_flag

            settings = Settings(strict=False, xml_huge_tree=True)
            try:
                client = Client(wsdl=self._wsdl_url, settings=settings, transport=Transport(session=session))
            except Exception as e:
                logger.error(f"连接 WSDL 失败：{e}")
                self._notify("🔴 SCP 连接失败", str(e), success=False)
                return

            # 拉取列表
            try:
                vps_list = client.service.getVServers(loginName=self._customer, password=self._password)
                if not vps_list:
                    msg = "📭 未找到任何 VPS。"
                    logger.info(msg)
                    self._notify("🟡 无 VPS", msg, success=True)
                    return
            except Exception as e:
                logger.error(f"获取 VPS 列表失败：{e}")
                self._notify("🔴 获取列表失败", str(e), success=False)
                return

            throttled: List[str] = []

            def safe_get(obj, attr, default=None):
                if obj is None:
                    return default
                try:
                    value = getattr(obj, attr, default)
                    return value if value is not None else default
                except Exception:
                    return default

            def dump_info(name: str, info: Any):
                if not self._debug_dump:
                    return
                try:
                    logger.info(f"VPS[{name}] 返回：{info}")
                except Exception:
                    pass

            for vname in vps_list:
                try:
                    info = client.service.getVServerInformation(
                        loginName=self._customer,
                        password=self._password,
                        vservername=vname,
                        language=self._language
                    )
                    dump_info(vname, info)

                    ips = safe_get(info, 'ips', [])
                    primary_ip = ips[0] if ips and len(ips) > 0 else "未知"
                    interfaces = safe_get(info, 'serverInterfaces', [])

                    is_throttled = False
                    for iface in interfaces or []:
                        if hasattr(iface, 'trafficThrottled') and getattr(iface, 'trafficThrottled', False) is True:
                            is_throttled = True
                            break

                    logger.info(f"VPS {vname} -> IP: {primary_ip}, 限速: {'是' if is_throttled else '否'}")
                    if is_throttled:
                        throttled.append(f"• {vname} ({primary_ip})")

                except Exception as e:
                    logger.warning(f"获取 {vname} 信息失败：{e}")

            # 通知
            if throttled:
                title = "⚠️ VPS 被限速"
                content = "以下 VPS 当前被限速：\n" + "\n".join(throttled)
                self._notify(title, content, success=False)
            else:
                if self._notify_all_ok:
                    title = "🟢 所有 VPS 正常"
                    content = f"共 {len(vps_list)} 台 VPS，均未被限速。"
                    self._notify(title, content, success=True)

    def start_device_flow(self, req: Optional[dict] = None):
        """生成设备码，返回带 user_code 的验证链接（可选 account id 回显）"""
        try:
            import requests
            acc_id = None
            if isinstance(req, dict):
                acc_id = req.get('id') or req.get('account_id')
            resp = requests.post(
                'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/auth/device',
                data={'client_id': 'scp', 'scope': 'offline_access openid'}, timeout=15
            )
            resp.raise_for_status()
            data = resp.json() or {}
            return {
                'code': 200,
                'message': 'OK',
                'data': {
                    'device_code': data.get('device_code'),
                    'user_code': data.get('user_code'),
                    'verification_uri': data.get('verification_uri'),
                    'verification_uri_complete': data.get('verification_uri_complete'),
                    'expires_in': data.get('expires_in'),
                    'interval': data.get('interval'),
                    'account_id': acc_id
                }
            }
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def poll_device_token(self, device_code: Optional[dict] = None):
        """轮询获取设备码令牌（支持为指定账户保存 token）"""
        try:
            import requests, time
            req = device_code or {}
            dc = req.get('device_code') if isinstance(req, dict) else None
            acc_id = req.get('id') if isinstance(req, dict) else None
            if not acc_id and isinstance(req, dict):
                acc_id = req.get('account_id')
            resp = requests.post(
                'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/token',
                data={
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                    'device_code': dc,
                    'client_id': 'scp'
                }, timeout=15
            )
            if resp.status_code != 200:
                return {'code': 202, 'message': resp.text}
            data = resp.json() or {}
            access_token = data.get('access_token')
            refresh_token = data.get('refresh_token')
            expires_in = data.get('expires_in') or 300
            import time as _t
            expires_at = int(_t.time()) + int(expires_in)

            # 若传入账户 ID，则保存至指定账户；否则保存至全局字段（兼容旧行为）
            saved_to = 'global'
            if acc_id and isinstance(self._accounts, list):
                for a in self._accounts:
                    if a.get('id') == acc_id:
                        a['rest_access_token'] = access_token
                        a['rest_refresh_token'] = refresh_token
                        a['rest_token_expires_at'] = expires_at
                        saved_to = f"account:{acc_id}"
                        break
            if saved_to == 'global':
                self._rest_access_token = access_token
                self._rest_refresh_token = refresh_token
                self._rest_token_expires_at = expires_at
            self.__update_config()
            return {'code': 200, 'message': 'ok', 'data': {'saved_to': saved_to}}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def revoke_device_token(self, req: Optional[dict] = None):
        """撤销令牌并清除本地（可指定账户）"""
        try:
            import requests
            acc_id = None
            if isinstance(req, dict):
                acc_id = req.get('id') or req.get('account_id')

            if acc_id and isinstance(self._accounts, list):
                for a in self._accounts:
                    if a.get('id') == acc_id:
                        rt = a.get('rest_refresh_token')
                        if rt:
                            requests.post(
                                'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/revoke',
                                data={'client_id': 'scp', 'token': rt, 'token_type_hint': 'refresh_token'}, timeout=15
                            )
                        a['rest_access_token'] = None
                        a['rest_refresh_token'] = None
                        a['rest_token_expires_at'] = None
                        self.__update_config()
                        return {'code': 200, 'message': 'revoked', 'data': {'scope': f'account:{acc_id}'}}

            # 全局回退
            if self._rest_refresh_token:
                requests.post(
                    'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/revoke',
                    data={'client_id': 'scp', 'token': self._rest_refresh_token, 'token_type_hint': 'refresh_token'}, timeout=15
                )
            self._rest_access_token = None
            self._rest_refresh_token = None
            self._rest_token_expires_at = None
            self.__update_config()
            return {'code': 200, 'message': 'revoked', 'data': {'scope': 'global'}}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    # ===== 多账户：新增 / 删除 / 更新 =====
    def account_add(self, req: Optional[dict] = None):
        """新增账户：入参 {name}，返回 data.id"""
        try:
            import uuid
            name = None
            if isinstance(req, dict):
                name = req.get('name')
            if not name:
                name = '新账户'
            acc = {
                'id': uuid.uuid4().hex,
                'name': str(name),
                'enabled': True,
                'api_mode': 'rest',
                'rest_access_token': None,
                'rest_refresh_token': None,
                'rest_token_expires_at': None,
            }
            if not isinstance(self._accounts, list):
                self._accounts = []
            self._accounts.append(acc)
            self.__update_config()
            return {'code': 200, 'message': 'ok', 'data': acc}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def account_remove(self, req: Optional[dict] = None):
        """删除账户：入参 {id}"""
        try:
            if not isinstance(req, dict):
                return {'code': 400, 'message': 'id required'}
            acc_id = req.get('id')
            if not acc_id:
                return {'code': 400, 'message': 'id required'}
            if not isinstance(self._accounts, list):
                self._accounts = []
            self._accounts = [a for a in self._accounts if a.get('id') != acc_id]
            self.__update_config()
            return {'code': 200, 'message': 'ok'}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def account_update(self, req: Optional[dict] = None):
        """更新账户：入参 {id, name?, enabled?, api_mode?, customer?, password?}"""
        try:
            if not isinstance(req, dict):
                return {'code': 400, 'message': 'invalid request'}
            acc_id = req.get('id')
            if not acc_id or not isinstance(self._accounts, list):
                return {'code': 400, 'message': 'id required'}
            for a in self._accounts:
                if a.get('id') == acc_id:
                    if 'name' in req:
                        a['name'] = str(req.get('name') or '')
                    if 'enabled' in req:
                        a['enabled'] = bool(req.get('enabled'))
                    if 'api_mode' in req:
                        mode = str(req.get('api_mode') or 'rest').lower()
                        a['api_mode'] = 'soap' if mode == 'soap' else 'rest'
                    # SOAP 凭据
                    if 'customer' in req:
                        a['customer'] = req.get('customer')
                    if 'password' in req:
                        a['password'] = req.get('password')
                    self.__update_config()
                    return {'code': 200, 'message': 'ok'}
            return {'code': 404, 'message': 'account not found'}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def _notify(self, title: str, content: str, success: bool = True):
        try:
            eventmanager.send_event(EventType.NoticeMessage, {
                "title": title,
                "text": content,
                "type": NotificationType.Manual
            })
        except Exception as e:
            logger.error(f"发送通知失败：{e}")
