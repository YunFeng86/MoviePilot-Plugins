# OneBot消息通知插件

OneBot消息通知插件是一个基于OneBot v11协议的消息推送渠道，用于MoviePilot系统的消息推送。

## 功能特点

- 支持私聊消息和群消息两种消息类型
- 支持与各种基于OneBot v11协议的机器人框架对接
- 支持消息类型过滤，可自定义接收哪些类型的通知
- 支持访问令牌认证

## 配置说明

1. **OneBot服务器**: OneBot兼容实现的HTTP API地址，例如 `http://localhost:5700`
2. **访问令牌(可选)**: 如果OneBot服务设置了access_token，需要在此填写
3. **消息类型**: 选择`私聊消息`或`群组消息`
4. **用户ID**: 当选择`私聊消息`时，填写接收消息的私聊账号
5. **群组ID**: 当选择`群组消息`时，填写接收消息的群号
6. **消息类型过滤**: 可选择接收哪些类型的通知消息

## 支持的OneBot实现

- [go-cqhttp](https://github.com/Mrs4s/go-cqhttp)
- [OneBot Kotlin](https://github.com/yyuueexxiinngg/onebot-kotlin)
- [Mirai](https://github.com/mamoe/mirai) + [Mirai-api-http](https://github.com/project-mirai/mirai-api-http)
- 其他遵循OneBot v11标准的实现

## 使用示例

### 配置go-cqhttp

1. 下载并安装 [go-cqhttp](https://github.com/Mrs4s/go-cqhttp/releases)
2. 配置 `config.yml` 文件，开启 HTTP 服务：

```yaml
servers:
  - http:
      host: 0.0.0.0
      port: 5700
      timeout: 5
      post:
      - url: '' # 可以不填
        secret: ''
      middlewares:
        <<: *default
      post-format: string
      secret: '' # 访问密钥，如果设置了需要在插件中填写
```

3. 启动go-cqhttp

4. 在MoviePilot的插件设置中配置OneBot消息通知插件，填写相应参数

## 注意事项

1. 确保OneBot服务能够被MoviePilot服务器访问
2. 如果使用Docker部署，需要注意网络配置
3. 私聊账号和群号必须是纯数字
4. 访问令牌需要与OneBot服务的设置保持一致

## 常见问题

1. **消息发送失败**: 检查服务器地址、端口是否正确，以及网络连接是否正常
2. **认证失败**: 检查access_token是否与OneBot服务设置一致
3. **找不到用户/群组**: 确认用户ID或群组ID是否正确，并确认机器人账号是否有权限发送消息

## 更新历史

- v1.0.0: 初始版本，支持基本的消息推送功能
