# 安装与迁移

## 1. 获取仓库

```powershell
New-Item -ItemType Directory -Force -Path "D:\CodexLocalAI\repositories" | Out-Null
git clone https://github.com/3322810958-sudo/agent-skill.git "D:\CodexLocalAI\repositories\agent-skill"
```

## 2. 恢复插件源码

将仓库中的两个插件目录复制到：

```text
D:\CodexLocalAI\plugins\local-ai-router
D:\CodexLocalAI\plugins\intent-refiner
```

安装或注册插件时，应使用当前 Codex 版本支持的个人插件或本地 marketplace 方法。不要复制 Codex 官方插件缓存，也不要把账号凭据写入仓库。

`local-ai-router` 还要求本机已有：

- `D:\CodexLocalAI\local-ai.ps1`
- 可工作的本地模型运行环境
- 与脚本一致的模型路径和端口

本仓库不提供模型和运行程序。模型来源见 `MODEL-DOWNLOADS.md`。

## 3. 验证

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\CodexLocalAI\repositories\agent-skill\scripts\publish-custom-plugins.ps1" -DryRun
```

看到两个插件均通过清单、路径、扩展名和敏感信息检查后，再执行正式同步：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\CodexLocalAI\repositories\agent-skill\scripts\publish-custom-plugins.ps1" -Push
```

## 4. 新增自制插件

新插件必须先确认完全由你拥有发布权，再把名称和 D 盘源码路径加入 `config/publish-allowlist.json`。新增后先执行 `-DryRun`。第三方插件不得加入白名单。

