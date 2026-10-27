---
title: Copilot 费用说明
summary: 一条消息为什么会花上百 credits，以及怎样省。
group: Working
icon: coins
order: 25
---
## Copilot 现在怎么计费

GitHub Copilot 从 2026 年 6 月起按 **token** 计费，单位是 GitHub AI Credits（1 credit = $0.01）。
Business 计划每人每月含 $19 的额度，在组织层面汇总；用完后按同样单价计费。
以前"每条消息算一次"的 premium request 已经不存在了。

每次模型调用的费用 = 输入 token × 单价 + cached 输入 token × 折扣单价 + 输出 token（含推理）× 单价。
GPT-6.1 Sol 的默认档单价：输入 $2 / 百万 token，cached 输入 $0.10，输出 $10。

## 一条消息为什么会花上百 credits

- 助手回答一条消息往往要跑十几轮：读文件、跑命令、再看结果。**每一轮都会把整段上下文重新发一遍**（系统提示、工具定义、历史、工具输出）。
- 15 轮、每轮 10 万 token 的上下文，就是 150 万输入 token；cache 命中时约 1 元，命中不了就是 4 元以上。
- 推理（thinking）算输出 token，单价是输入的 5 倍。High 强度会多出不少推理 token。

每条回答下方的一行会告诉你这次花了什么：轮数、输入 / 输出 token、cache 命中率、估算的 credits。
把鼠标放上去可以看到计费档位和模型。估算按 GitHub 公开价目表计算，不是账单本身。

## 272K 这条线

单次请求的输入超过 **272K token**，GPT-6 系列进入 Long context 档，**所有单价翻倍**。
Context 面板的表盘上标出了这条线；越过它之后，表盘变成警告色，面板里显示"Long context 档"。

上下文大小预设里，1M 标注了"Long context 2×"，选它要知道这意味着什么。

## 怎样省

1. **新话题开新会话。** 上下文越长，每条消息越贵；旧会话里的工具输出会一直跟着。
2. **少贴大文件。** 贴进来的内容每一轮都会重发；让助手自己读需要的部分更省。
3. **推理强度用 Medium。** 日常任务够用；需要时再在 Run settings 里按会话调成 High。
4. **模型用 Auto。** Copilot 会给简单的请求选便宜的模型，并有 10% 折扣。
5. **注意"已暂停"提示。** 一条消息超过 credits 上限或轮数上限时，助手会停下来说明；确认还要继续再点"继续"。

## 查看额度

- Context 面板里的"本月 Copilot 额度"显示已用 / 剩余与重置日期（来自 GitHub 的额度接口，不保证总是可用）。
- 助手详情里的 Usage (30 days) 显示近 30 天的请求数、token 与估算 credits。
- 权威数字在 GitHub 的 Billing 页面：[Models and pricing for GitHub Copilot](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing)。
