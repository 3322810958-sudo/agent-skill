# agent-skill

用于保存和迁移个人自制的 Codex 插件与 Skill。仓库只收录作者拥有发布权的内容；第三方 Skill、模型、运行程序、缓存、日志、凭据和个人数据均不复制进来。

## 包含内容

- `plugins/local-ai-router`：把适合的机械性代码草稿、拆分、提取和总结交给 `D:\CodexLocalAI` 中的本地模型，再由 Codex 独立复核。
- `plugins/intent-refiner`：结合上下文理解口语化或零散需求，只在关键歧义会改变结果时澄清。
- `scripts/publish-custom-plugins.ps1`：使用严格白名单同步自制插件，执行清单校验、敏感信息检查、提交和推送。
- `docs/THIRD-PARTY-SOURCES.md`：第三方 Skill 的官方来源链接，仅作安装索引。
- `docs/MODEL-DOWNLOADS.md`：本地模型的官方下载入口，不包含模型文件。

## 安全边界

- 只同步 `config/publish-allowlist.json` 中明确列出的插件和文件类型。
- 默认阻止凭据、私钥、收款图片、模型、日志、缓存、临时目录和二进制媒体。
- 检查失败时立即停止，不提交、不推送，也不显示疑似密钥的值。
- 第三方 Skill 必须从上游仓库安装；本仓库不重新分发其源码。

## 使用

安装与迁移见 [`docs/INSTALLATION.md`](docs/INSTALLATION.md)。更新发布示例：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish-custom-plugins.ps1 -Push
```

首次验证可先使用 `-DryRun`，不会复制、提交或推送文件。

## 许可证

本仓库自制内容采用 MIT License。第三方项目保持各自许可证，本仓库只提供链接。

