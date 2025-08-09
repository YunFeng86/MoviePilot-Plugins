from typing import Any, List, Dict, Tuple, Optional, Union
import json
import time
from datetime import datetime
from urllib.parse import urlencode

from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.http import RequestUtils


class OneBotMsg(_PluginBase):
    # 插件名称
    plugin_name = "OneBot消息通知"
    # 插件描述
    plugin_desc = "支持使用OneBot v11协议发送消息通知。"
    # 插件图标
    plugin_icon = "https://img.seedvault.cn/i/2025/08/09/logo68972f3d5dcbc40.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "YunFeng"
    # 作者主页
    author_url = "https://github.com/YunFeng86"
    # 插件配置项ID前缀
    plugin_config_prefix = "onebotmsg_"
    # 加载顺序
    plugin_order = 28
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    _server = None
    _access_token = None
    _user_id = None
    _group_id = None
    _message_type = None
    _msgtypes = []

    def init_plugin(self, config: Optional[dict] = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._msgtypes = config.get("msgtypes") or []
            self._server = config.get("server")
            self._access_token = config.get("access_token")
            self._user_id = config.get("user_id")
            self._group_id = config.get("group_id")
            self._message_type = config.get("message_type")

        # 发送测试消息（如果是通过修改配置启用的）
        if config and config.get("onlyonce"):
            self._send("OneBot消息测试通知", "OneBot消息通知插件已启用")
            # 重置onlyonce标志
            self.update_config({
                "enabled": self._enabled,
                "onlyonce": False,
                "msgtypes": self._msgtypes,
                "server": self._server,
                "access_token": self._access_token,
                "user_id": self._user_id,
                "group_id": self._group_id,
                "message_type": self._message_type
            })

    def get_state(self) -> bool:
        """检查插件是否配置正确且已启用"""
        if not self._enabled or not self._server:
            return False
            
        # 检查消息类型配置
        if self._message_type == "private":
            return bool(self._user_id)
        elif self._message_type == "group":
            return bool(self._group_id)
        
        return False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 编历 NotificationType 枚举，生成消息类型选项
        msg_type_options = []
        for item in NotificationType:
            msg_type_options.append({
                "title": item.value,
                "value": item.name
            })
        
        message_type_options = [
            {
                "title": "私聊消息",
                "value": "private"
            },
            {
                "title": "群组消息",
                "value": "group"
            }
        ]
        
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '测试插件（立即运行）',
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'server',
                                            'label': 'OneBot服务器',
                                            'placeholder': 'http://localhost:5700',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'access_token',
                                            'label': '访问令牌（出于安全原因，请尽量设置 AccessToken）',
                                            'placeholder': '如果OneBot设置了access_token，请在此填写',
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
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'message_type',
                                            'label': '消息类型',
                                            'items': message_type_options
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'user_id',
                                            'label': '用户ID',
                                            'placeholder': '发送私聊消息时的用户ID',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'group_id',
                                            'label': '群组ID',
                                            'placeholder': '发送群组消息时的群组ID',
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
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'model': 'msgtypes',
                                            'label': '消息类型',
                                            'items': msg_type_options
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            'msgtypes': [],
            'server': 'http://localhost:5700',
            'access_token': '',
            'user_id': '',
            'group_id': '',
            'message_type': 'private',
        }

    def get_page(self) -> Optional[List[dict]]:
        """拼装插件详情页面，显示OneBot状态"""
        # 获取状态信息
        enabled_status = "已启用" if self.get_state() else "未启用"
        status_color = "success" if self.get_state() else "error"
        
        # 服务器状态
        server_status = self._server if self._server else "未配置"
        
        # 消息类型
        message_type_text = "私聊消息" if self._message_type == "private" else "群组消息"
        target_id = self._user_id if self._message_type == "private" else self._group_id
        target_id_text = target_id if target_id else "未配置"
        
        # 允许的消息类型
        allowed_types = []
        for msg_type in NotificationType:
            if not self._msgtypes or msg_type.name in self._msgtypes:
                allowed_types.append(msg_type.value)
        allowed_types_text = "、".join(allowed_types) if allowed_types else "无"

        # 构建页面
        return [
            {
                'component': 'div',
                'props': {
                    'class': 'pa-4'
                },
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VCard',
                                        'props': {
                                            'class': 'mb-4'
                                        },
                                        'content': [
                                            {
                                                'component': 'VCardTitle',
                                                'props': {
                                                    'class': 'text-h5'
                                                },
                                                'text': 'OneBot消息通知状态'
                                            },
                                            {
                                                'component': 'VCardText',
                                                'content': [
                                                    {
                                                        'component': 'VRow',
                                                        'content': [
                                                            {
                                                                'component': 'VCol',
                                                                'props': {
                                                                    'cols': 12,
                                                                    'md': 6
                                                                },
                                                                'content': [
                                                                    {
                                                                        'component': 'VList',
                                                                        'props': {
                                                                            'dense': True
                                                                        },
                                                                        'content': [
                                                                            {
                                                                                'component': 'VListItem',
                                                                                'content': [
                                                                                    {
                                                                                        'component': 'VListItemTitle',
                                                                                        'content': [
                                                                                            {
                                                                                                'component': 'strong',
                                                                                                'text': '状态：'
                                                                                            },
                                                                                            {
                                                                                                'component': 'VChip',
                                                                                                'props': {
                                                                                                    'color': status_color,
                                                                                                    'small': True,
                                                                                                    'class': 'ml-1'
                                                                                                },
                                                                                                'text': enabled_status
                                                                                            }
                                                                                        ]
                                                                                    }
                                                                                ]
                                                                            },
                                                                            {
                                                                                'component': 'VListItem',
                                                                                'content': [
                                                                                    {
                                                                                        'component': 'VListItemTitle',
                                                                                        'content': [
                                                                                            {
                                                                                                'component': 'strong',
                                                                                                'text': '服务器：'
                                                                                            },
                                                                                            {
                                                                                                'component': 'span',
                                                                                                'text': server_status
                                                                                            }
                                                                                        ]
                                                                                    }
                                                                                ]
                                                                            },
                                                                            {
                                                                                'component': 'VListItem',
                                                                                'content': [
                                                                                    {
                                                                                        'component': 'VListItemTitle',
                                                                                        'content': [
                                                                                            {
                                                                                                'component': 'strong',
                                                                                                'text': '消息类型：'
                                                                                            },
                                                                                            {
                                                                                                'component': 'span',
                                                                                                'text': message_type_text
                                                                                            }
                                                                                        ]
                                                                                    }
                                                                                ]
                                                                            },
                                                                            {
                                                                                'component': 'VListItem',
                                                                                'content': [
                                                                                    {
                                                                                        'component': 'VListItemTitle',
                                                                                        'content': [
                                                                                            {
                                                                                                'component': 'strong',
                                                                                                'text': '目标ID：'
                                                                                            },
                                                                                            {
                                                                                                'component': 'span',
                                                                                                'text': target_id_text
                                                                                            }
                                                                                        ]
                                                                                    }
                                                                                ]
                                                                            },
                                                                            {
                                                                                'component': 'VListItem',
                                                                                'content': [
                                                                                    {
                                                                                        'component': 'VListItemTitle',
                                                                                        'content': [
                                                                                            {
                                                                                                'component': 'strong',
                                                                                                'text': '接收消息类型：'
                                                                                            },
                                                                                            {
                                                                                                'component': 'span',
                                                                                                'text': allowed_types_text
                                                                                            }
                                                                                        ]
                                                                                    }
                                                                                ]
                                                                            }
                                                                        ]
                                                                    }
                                                                ]
                                                            },
                                                            {
                                                                'component': 'VCol',
                                                                'props': {
                                                                    'cols': 12,
                                                                    'md': 6
                                                                }
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
                    }
                ]
            }
        ]

    def get_dashboard_meta(self) -> Optional[List[Dict[str, str]]]:
        """
        获取插件仪表盘元信息
        """
        return [{
            "key": "default",
            "name": "OneBot消息通知状态"
        }]
        
    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], Optional[List[dict]]]]:
        """
        获取插件仪表盘页面，展示OneBot消息发送状态和历史记录
        """
        # 仪表板布局配置
        col_dict = {
            "cols": 12
        }
        
        # 全局配置
        config_dict = {
            "title": "OneBot消息通知状态",
            "subtitle": "显示当前配置状态和最近的消息发送记录",
            "refresh": 30  # 30秒自动刷新一次
        }
        
        # 构建状态信息
        status_items = []
        
        # 基本状态
        enabled_status = "已启用" if self.get_state() else "未启用"
        status_color = "success" if self.get_state() else "error"
        
        # 服务器状态
        server_status = self._server if self._server else "未配置"
        
        # 消息类型
        message_type_text = "私聊消息" if self._message_type == "private" else "群组消息"
        target_id = self._user_id if self._message_type == "private" else self._group_id
        target_id_text = target_id if target_id else "未配置"
        
        # 允许的消息类型
        allowed_types = []
        for item in NotificationType:
            if item.name in self._msgtypes:
                allowed_types.append(item.value)
        allowed_types_text = "、".join(allowed_types) if allowed_types else "未设置（接收所有类型）"
        
        # 仪表板内容
        content = [
            {
                'component': 'VCard',
                'content': [
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VRow',
                                'content': [
                                    {
                                        'component': 'VCol',
                                        'props': {
                                            'cols': 12
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-h6 mb-2 d-flex align-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'span',
                                                        'text': 'OneBot状态'
                                                    },
                                                    {
                                                        'component': 'VChip',
                                                        'props': {
                                                            'color': status_color,
                                                            'class': 'ml-2',
                                                            'small': True
                                                        },
                                                        'text': enabled_status
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VDivider',
                                                'props': {
                                                    'class': 'mb-3'
                                                }
                                            },
                                            {
                                                'component': 'VList',
                                                'props': {
                                                    'dense': True
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'content': [
                                                                    {
                                                                        'component': 'strong',
                                                                        'text': '服务器地址：'
                                                                    },
                                                                    {
                                                                        'component': 'span',
                                                                        'text': server_status
                                                                    }
                                                                ]
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'content': [
                                                                    {
                                                                        'component': 'strong',
                                                                        'text': '消息类型：'
                                                                    },
                                                                    {
                                                                        'component': 'span',
                                                                        'text': message_type_text
                                                                    }
                                                                ]
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'content': [
                                                                    {
                                                                        'component': 'strong',
                                                                        'text': '目标ID：'
                                                                    },
                                                                    {
                                                                        'component': 'span',
                                                                        'text': target_id_text
                                                                    }
                                                                ]
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'content': [
                                                                    {
                                                                        'component': 'strong',
                                                                        'text': '接收消息类型：'
                                                                    },
                                                                    {
                                                                        'component': 'span',
                                                                        'text': allowed_types_text
                                                                    }
                                                                ]
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VBtn',
                                                'props': {
                                                    'color': 'primary',
                                                    'to': '/plugins?tab=installed&id=OneBotMsg&settings=1',
                                                    'class': 'mt-3'
                                                },
                                                'text': '修改配置'
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
        
        return col_dict, config_dict, content
        


    def _send(self, title: Optional[str], text: Optional[str]) -> Optional[Tuple[bool, str]]:
        """发送消息"""
        if not self.get_state():
            return False, "插件未启用或参数未配置"
        
        try:
            # 构建消息内容
            title_str = title or ""
            text_str = text or ""
            message = f"{title_str}\n\n{text_str}" if title_str else text_str
            
            # 确定发送的API接口
            if not self._server:
                return False, "服务器地址未配置"
                
            if self._message_type == "private":
                api_endpoint = f"{self._server.rstrip('/')}/send_private_msg"
                if not self._user_id:
                    return False, "用户ID未配置"
                try:
                    user_id = int(self._user_id)
                except (ValueError, TypeError):
                    return False, f"用户ID格式错误: {self._user_id}"
                    
                params = {
                    "user_id": user_id,
                    "message": message
                }
            else:  # group
                api_endpoint = f"{self._server.rstrip('/')}/send_group_msg"
                if not self._group_id:
                    return False, "群组ID未配置"
                try:
                    group_id = int(self._group_id)
                except (ValueError, TypeError):
                    return False, f"群组ID格式错误: {self._group_id}"
                    
                params = {
                    "group_id": group_id,
                    "message": message
                }
            
            # 构建请求头
            headers = {}
            if self._access_token:
                headers["Authorization"] = f"Bearer {self._access_token}"
            
            # 发送请求
            res = RequestUtils(headers=headers).post_res(
                api_endpoint,
                json=params
            )
            
            if res and res.status_code == 200:
                res_json = res.json()
                if res_json.get("status") == "ok" and res_json.get("retcode") == 0:
                    logger.info(f"OneBot消息发送成功: {self._message_type}, 消息内容：{title_str} - {text_str}")
                    return True, "发送成功"
                else:
                    error_msg = res_json.get('msg', res_json.get('message', 'unknown error'))
                    logger.warning(f"OneBot消息发送失败: {error_msg}")
                    return False, f"发送失败: {error_msg}"
            elif res is not None:
                logger.warning(f"OneBot消息发送失败，HTTP错误码：{res.status_code}，错误原因：{res.reason}")
                return False, f"发送失败，HTTP错误码：{res.status_code}，错误原因：{res.reason}"
            else:
                logger.warning("OneBot消息发送失败：未获取到返回信息")
                return False, "发送失败：未获取到返回信息"
                
        except Exception as e:
            logger.error(f"OneBot消息发送异常: {str(e)}")
            return False, f"发送异常: {str(e)}"

    @eventmanager.register(EventType.NoticeMessage)
    def send(self, event: Event):
        """消息发送事件"""
        if not self.get_state() or not event.event_data:
            return

        msg_body = event.event_data
        # 检查msg_body是否为字典类型
        if not isinstance(msg_body, dict):
            logger.error(f"消息格式错误: {msg_body}")
            return
            
        # 渠道
        channel = msg_body.get("channel")
        if channel:
            return
            
        # 类型
        msg_type_value = msg_body.get("type")
        # 验证msg_type是否为NotificationType枚举
        msg_type = None
        if msg_type_value:
            for item in NotificationType:
                if item.name == msg_type_value or item.value == msg_type_value:
                    msg_type = item
                    break
        
        # 标题和文本
        title = msg_body.get("title")
        text = msg_body.get("text")

        if not title and not text:
            logger.warning("标题和内容不能同时为空")
            return

        # 检查消息类型是否在允许列表中
        if (msg_type and self._msgtypes
                and msg_type.name not in self._msgtypes):
            logger.info(f"消息类型 {msg_type.value if msg_type else '未知'} 未开启消息发送")
            return

        return self._send(title, text)

    def stop_service(self):
        """退出插件"""
        pass