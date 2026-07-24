# 本地模型与 Label Studio 接入

## 本地模型产线

创建产线时选择“本地模型”，上传一个可由 Ultralytics 加载并继续训练的 PyTorch 权重文件：

- 文件格式：`.pt`
- 任务类型：目标检测、图像分割、语义分割、关键点检测、旋转框检测或图像分类
- 模型规模：`n`、`s`、`m`、`l` 或 `x`
- 最大文件大小：默认 2 GiB，可通过 `VISIOX_MAX_MODEL_UPLOAD_BYTES` 调整

平台会流式上传文件、校验文件头、计算 SHA-256，并将权重保存到对象存储。上传完成后，平台立即创建一个“配置中”的产线草稿，并把上传的权重绑定为该产线的基础模型。之后继续完成数据集选择、参数配置和提交训练；训练产物仍进入原有评估、在线体验和部署流程。

`.onnx` 与 `.engine` 是推理或部署产物，不能作为当前训练产线的基础权重上传。它们应由部署流程从训练后的 `.pt` 权重自动导出。

## Label Studio 免登录入口

浏览器访问平台生成的 Label Studio 链接时，先进入 `label-studio-gateway`。网关使用平台配置的服务账号在服务端建立 Label Studio 会话，再把会话 Cookie 返回给浏览器并跳转到目标项目，用户不需要看到或填写 Label Studio 密码。

Compose 部署中应满足：

- `label-studio` 仅暴露内部 `8080` 端口
- `label-studio-gateway` 对外暴露平台配置的公共地址
- `VISIOX_LABEL_STUDIO_PUBLIC_URL` 指向网关，而不是 Label Studio 容器地址
- `LABEL_STUDIO_USERNAME` 与 `LABEL_STUDIO_PASSWORD` 只保存在服务端环境变量中

当前实现是单租户服务账号方案。进入多租户商业部署后，应替换为统一身份认证或按用户映射的短期会话，避免不同平台用户共享同一个 Label Studio 身份。
