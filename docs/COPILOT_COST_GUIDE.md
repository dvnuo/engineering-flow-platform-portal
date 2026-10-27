# Copilot 费用说明（给成员）

Portal 内的同一份说明在 Help → Working → "Copilot 费用说明"（`app/help/copilot-costs.md`）。

## 计费口径

- 2026-06-01 起 GitHub Copilot 按 token 计 GitHub AI Credits，1 credit = $0.01；Business 每人每月含 $19 额度，在组织层面汇总，用超后按同样单价计费。premium request（按次）只对年付 Pro 的存量用户保留。
- GPT-6.1 Sol 默认档：输入 $2.00 / 百万 token，cached 输入 $0.10，cache write $2.50，输出 $10（推理算输出）。单次请求输入超过 272K token 进入 Long context 档，全部翻倍。
- 官方价目表：<https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing>

## 一条消息为什么会花上百 credits

一条消息触发的 agent 循环会跑十几轮，每一轮都把整段上下文（系统提示、工具定义、历史、工具输出）重发一次。15 轮、每轮约 10 万 token 就是 150 万输入 token：cache 命中约 100 credits，命中不了 400 credits 以上，进入 Long context 档再翻倍。推理强度 High 还会多出按输出计费的推理 token。

## Portal 里能看到什么

| 位置 | 内容 |
| --- | --- |
| 每条回答下方 | 轮数、输入 / 输出 token、cache 命中率、估算 credits；悬停看计费档位与模型 |
| Context 面板 | 表盘上的 272K 计费线、当前档位、运行时实际预算、按实测 token 校准的用量、本会话累计 credits、本月 Copilot 额度 |
| 助手详情 Usage (30 days) | 请求数、输入 / 输出 / cached / 推理 token、估算 credits |
| "已暂停"提示 | 一条消息超过 credits 上限或轮数上限时助手停下说明，点"继续"接着跑 |

## 怎样省

1. 新话题开新会话：上下文越长每条消息越贵。
2. 少贴大文件：贴进来的内容每一轮都重发。
3. 推理强度默认 Medium：需要时再按会话调 High。
4. 模型用 Auto：简单请求走便宜模型，并有 10% 折扣。
5. 上下文预设 1M 标注"Long context 2×"，知道代价再选。
