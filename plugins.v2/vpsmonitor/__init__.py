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
    plugin_icon = "https://raw.githubusercontent.com/YunFeng86/MoviePilot-Plugins/main/icons/OneBot_A.png"
    plugin_version = "0.2.0"
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
                                            },
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {'component': 'VListItemTitle', 'content': [
                                                        {'component': 'strong', 'text': 'WSDL：'},
                                                        {'component': 'span', 'text': wsdl_text}
                                                    ]}
                                                ]
                                            },
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {'component': 'VListItemTitle', 'content': [
                                                        {'component': 'strong', 'text': '语言：'},
                                                        {'component': 'span', 'text': language_text}
                                                    ]}
                                                ]
                                            },
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {'component': 'VListItemTitle', 'content': [
                                                        {'component': 'strong', 'text': '不安全 TLS：'},
                                                        {'component': 'span', 'text': insecure_text}
                                                    ]}
                                                ]
                                            },
                                            {
                                                'component': 'VListItem',
                                                'content': [
                                                    {'component': 'VListItemTitle', 'content': [
                                                        {'component': 'strong', 'text': '发送“全部正常”通知：'},
                                                        {'component': 'span', 'text': notify_ok_text}
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
        # 构造获取验证链接/撤销授权按钮的 onclick JS
        import json as _json
        js_api_token = _json.dumps(settings.API_TOKEN)
        # 单行ASCII，开始设备码并轮询令牌
        onclick_get_js = (
            "(function(){var apiKey=" + js_api_token + ";"
            "fetch('/api/v1/plugin/VPSMonitor/start_device_flow?apikey='+encodeURIComponent(apiKey),{method:'POST'})"
            ".then(function(r){return r.json()}).then(function(ret){if(!(ret&&ret.code===200&&ret.data)){alert('start failed:'+((ret&&ret.message)||''));return;}"
            "// no user_code alert"
            "if(ret.data.verification_uri_complete){window.open(ret.data.verification_uri_complete,'_blank');}"
            "var dc=ret.data.device_code;var end=Date.now()+((ret.data.expires_in||600)*1000);var iv=(ret.data.interval||5)*1000;"
            "(function poll(){if(Date.now()>end){alert('Authorization timeout');return;}"
            "fetch('/api/v1/plugin/VPSMonitor/poll_device_token?apikey='+encodeURIComponent(apiKey),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device_code:dc})})"
            ".then(function(r){return r.json()}).then(function(p){if(p&&p.code===200){alert('Authorized. Tokens saved.');var b=document.getElementById('vpsmonitor-auth-btn');if(b){b.textContent='取消授权';}return;}setTimeout(poll,iv);}).catch(function(e){setTimeout(poll,iv);});})();"
            "});})()"
        )
        # 撤销授权按钮JS
        onclick_revoke_js = (
            "(function(){var apiKey=" + js_api_token + ";"
            "fetch('/api/v1/plugin/VPSMonitor/revoke_device_token?apikey='+encodeURIComponent(apiKey),{method:'POST'})"
            ".then(function(r){return r.json()}).then(function(ret){if(ret&&ret.code===200){alert('Revoked.');var b=document.getElementById('vpsmonitor-auth-btn');if(b){b.textContent='获取验证链接';}}else{alert('Revoke failed:'+((ret&&ret.message)||''));}})"
            ".catch(function(e){alert('Request failed:'+e);});})()"
        )

        return [
            {
                'component': 'VForm',
                'content': [
                    # 行：REST 设备码按钮（右）
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VAlert',
                                    'props': {
                                        'type': 'info',
                                        'variant': 'tonal',
                                        'density': 'compact',
                                        'border': 'start',
                                        'color': 'primary'
                                    },
                                    'text': 'REST 基址：https://www.servercontrolpanel.de/scp-core（已固定）',
                                    'show': "{{ api_mode == 'rest' }}"
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6, 'class': 'd-flex justify-end'},
                                'content': [{
                                    'component': 'VBtn',
                                    'props': {
                                        'color': 'primary',
                                        'variant': 'elevated',
                                        'class': 'mt-2',
                                        'onclick': (onclick_revoke_js if (self._api_mode == 'rest' and self._rest_access_token) else onclick_get_js),
                                        'id': 'vpsmonitor-auth-btn',
                                        'show': "{{ api_mode == 'rest' }}"
                                    },
                                    'text': ('取消授权' if (self._api_mode == 'rest' and self._rest_access_token) else '获取验证链接')
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
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cron',
                                        'label': 'Cron 定时（5 字段）',
                                        'placeholder': '*/10 * * * *',
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
                    
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'rest_token',
                                        'label': 'REST Access Token (Bearer)',
                                        'placeholder': '在上方文档说明中通过设备码流程获取的 access_token',
                                        'show': "{{ api_mode == 'rest' }}"
                                    }
                                }]
                            },
                            
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify_all_ok',
                                            'label': '发送“全部正常”通知',
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
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'insecure_tls',
                                        'label': '不安全 TLS（跳过证书校验）',
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
                                        'label': '调试日志（输出完整返回）',
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
        }

    # ============ 内部实现 ============
    def _run_check(self):
        """执行一次检测"""
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

        # ========== 尝试 REST 模式 ==========
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
                if not self._rest_access_token:
                    raise Exception("未配置 REST Access Token (Bearer)")
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
                        interfaces = itf_json.get('interfaces')
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
                logger.error(f"REST 调用失败：{e}")
                self._notify("🔴 REST 调用失败", str(e), success=False)
                return

    def start_device_flow(self):
        """生成设备码，返回带 user_code 的验证链接"""
        try:
            import requests
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
                    'interval': data.get('interval')
                }
            }
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def poll_device_token(self, device_code: dict = None):
        """轮询获取设备码令牌"""
        try:
            import requests, time
            req = device_code or {}
            dc = req.get('device_code') if isinstance(req, dict) else None
            if not dc:
                from fastapi import Request
                try:
                    # 适配FastAPI传参
                    dc = Request.scope.get('query_string')
                except Exception:
                    pass
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
            self._rest_access_token = data.get('access_token')
            self._rest_refresh_token = data.get('refresh_token')
            expires_in = data.get('expires_in') or 300
            import time as _t
            self._rest_token_expires_at = int(_t.time()) + int(expires_in)
            self.__update_config()
            return {'code': 200, 'message': 'ok'}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

    def revoke_device_token(self):
        """撤销令牌并清除本地"""
        try:
            import requests
            if self._rest_refresh_token:
                requests.post(
                    'https://www.servercontrolpanel.de/realms/scp/protocol/openid-connect/revoke',
                    data={'client_id': 'scp', 'token': self._rest_refresh_token, 'token_type_hint': 'refresh_token'}, timeout=15
                )
            self._rest_access_token = None
            self._rest_refresh_token = None
            self._rest_token_expires_at = None
            self.__update_config()
            return {'code': 200, 'message': 'revoked'}
        except Exception as e:
            return {'code': 500, 'message': f'{e}'}

        # ========== SOAP 路径 ==========
        # SOAP 需要凭据
        if not self._customer or not self._password:
            logger.warning("VPS 监控未配置 SCP 凭据（SOAP 模式）")
            self._notify("🔴 VPS 监控未配置", "请填写 SCP 客户号与密码（SOAP）", success=False)
            return

        # 自定义 TLS 适配器（可选）
        insecure_flag = self._insecure_tls
        class TLSAdapter(HTTPAdapter):
            def __init__(self, insecure: bool = False, *args, **kwargs):
                # 注意顺序：先设置属性，再调用父类 __init__，
                # 因为父类 __init__ 会调用 init_poolmanager
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

    def _notify(self, title: str, content: str, success: bool = True):
        try:
            eventmanager.send_event(EventType.NoticeMessage, {
                "title": title,
                "text": content,
                "type": NotificationType.Manual
            })
        except Exception as e:
            logger.error(f"发送通知失败：{e}")
