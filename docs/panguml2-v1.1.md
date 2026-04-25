# panguml2.0格式定义

## 1. 概览

数据格式以 OpenAI 消息格式为参考，进行合理扩展。

## 2. 数据结构定义

### 2.1 顶级对象

| 属性 | 类型 | 约束 | 语义定义 |
| :--- | :--- | :--- | :--- |
| `messages` | `Array<Message>` | 必选 | 严格有序的对话序列 |
| `tools` | `Array<Object>` | 可选 | 声明当前上下文中模型可调用的工具/函数原型 |
| `version` | `String` | 必选 | 声明当前使用的规范版本号，如 “2.0.0“ |
| `meta_info` | `Object` | 必选 | 存储数据源和生产流程信息，详见[wiki](https://wiki.huawei.com/wiki/WIKI2026020410057757) |

### 2.2 消息对象 (Message)

消息对象的属性及其约束受 `role` 字段驱动，遵循以下逻辑分支：

#### 2.2.1 共有属性

* **`role`**: `Enum ["system", "user", "assistant", "tool"，"developer"]`。定义交互实体的功能身份。
* **`name`**: `String` (可选，向后兼容)。实体标识符，用于区分同角色下的多参与者，如果原始轨迹包含建议保留。

#### 2.2.2 逻辑分支规范

| 角色 (`role`) | 允许/必需属性 | 约束与语义 |
| :--- | :--- | :--- |
| **`system`** | `content` | 必须为 `String`，定义系统级行为约束 |
| **`user`** | `content` | 必须为 `String` |
| **`assistant`** | `content` `reasoning_content` `tool_calls` `weight（可选）` | 1. `content`: 必须存在，类型为 `String`，当存在 `tool_calls` 时，可为空字符串。 2. `reasoning_content`: 必须存在，类型为 `String`，快思考数据为空字符串。 3. `tool_calls`: `Array<ToolCall>`，模型生成的工具请求数组；其中 `id` 字段为可选，向后兼容工具并发调用。 4. `weight(可选)`：训练权重字段，向后兼容轮次 loss 计算控制，`0` 或 `1` |
| **`tool`** | `content` `tool_call_id` | 1. `tool_call_id`: 可选，必须匹配对应 `assistant.tool_calls[*].id`，并行工具调用场景建议补充，若原始轨迹中已有则建议保留，以便向后兼容。 2. `content`: 必选，必须为 `String` 或 `Array<TextPart>` 化的执行结果 |
| **`developer`** | `content` | 必须为 `String` 或 `Array<TextPart>`，定义开发者级指令，向后兼容保留角色 |

### 2.3 工具调用约束强调

1. `assistant.tool_calls[*].id` 与 `tool.tool_call_id` 均为可选字段；
2. 若 `assistant.tool_calls[*].id` 存在，则 `tool.tool_call_id` 若存在必须与之匹配；
3. 在并行工具调用场景下，建议补充 `id` 与 `tool_call_id`；若原始轨迹数据中已有，则建议保持保留，以便向后兼容；
4. `function.arguments` 建议为字符串化 JSON；
5. 若 assistant 发起工具调用，则其后续回复应以工具结果为依据，不得与工具结果矛盾。

## 3. 样例

训练文件 xx.jsonl 的任意一行应为如下格式：

```jsonc
{
  "version": "2.0.0",
  "meta_info": {
    "teacher": "glm-5-thinking",
    "query_source": "synthesized",
    "response_generate_time": "2026-03-03",
    "response_update_time": "2026-03-27",
    "owner": "00123456",
    "language": "zh",
    "category": "agent",
    "rounds": 1,
    "unique_info": {
      "info": {}
    }
  },
  "tools": [ // 工具定义，无工具调用时可省略
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的当前天气情况",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {
              "type": "string",
              "description": "城市标识，例如北京/Beijing、上海/Shanghai、深圳/Shenzhen，具体取值以工具接口约定为准"
            },
            "unit": {
              "type": "string",
              "enum": [
                "celsius",
                "fahrenheit"
              ],
              "description": "温度单位"
            }
          },
          "required": [
            "location"
          ]
        }
      }
    }
  ],
  "messages": [ // 对话消息序列
    {
      "role": "system",
      "content": "你是一个实时天气助手。"
    },
    {
      "role": "user",
      "content": "北京和深圳现在的天气怎么样？"
    },
    {
      "role": "assistant",
      "content": "", // 当存在 tool_calls 时可为空字符串
      "reasoning_content": "我需要先调用天气工具，分别查询北京和深圳的天气，再汇总回复用户。", // 快思考数据可为空字符串
      "tool_calls": [ // 工具请求数组
        {
          "id": "call_beijing_weather", // 可选；用于精确配对
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"location\":\"Beijing\"}"
          }
        },
        {
          "id": "call_shenzhen_weather",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"location\":\"Shenzhen\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_beijing_weather", // 可选；若存在应匹配上游 tool call id
      "content": "{\"location\":\"Beijing\",\"temperature\":\"22°C\",\"condition\":\"Sunny\"}"
    },
    {
      "role": "tool",
      "tool_call_id": "call_shenzhen_weather", // 可选；若存在应匹配上游 tool call id
      "content": "{\"location\":\"Shenzhen\",\"temperature\":\"29°C\",\"condition\":\"Sunny\"}"
    },
    {
      "role": "assistant",
      "content": "北京现在天气晴朗，气温约 22°C；深圳天气晴朗，气温约 29°C。"
    }
  ]
}
```