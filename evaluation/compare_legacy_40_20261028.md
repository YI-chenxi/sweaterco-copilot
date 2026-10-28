# Legacy Parser Shared-Capability Evaluation Report

- Generated: 2026-10-28T11:37:21+08:00
- Model: `gpt-6-luna`
- Provider: `openai`
- Evaluation mode: `live_legacy`
- Cases: 40
- Exact matches: 37/40 (92.5%)

- Comparison scope: shared capabilities only
- Shared checks: intent_sequence, slots, missing_slots

## Component Accuracy

| Component | Accuracy |
| --- | ---: |
| intent_sequence | 100.0% |
| slots | 92.5% |
| missing_slots | 100.0% |

## Task-Level Metrics

- Task intent accuracy: 40/40 (100.0%)
- Slot precision / recall / F1: 92.7% / 92.7% / 92.7%
- Missing-slot precision / recall / F1: 100.0% / 100.0% / 100.0%
- Slot-update exact cases: 0/0 (0.0%)
- Slot-update precision / recall / F1: 0.0% / 0.0% / 0.0%
- Ambiguity accuracy: 0/0 (0.0%)

## Error Categories

- slot_value_error: 3

## Accuracy by Category

| Category | Cases | Exact accuracy |
| --- | ---: | ---: |
| chinese | 6 | 83.3% |
| colloquial | 6 | 66.7% |
| english | 6 | 100.0% |
| missing_slots | 6 | 100.0% |
| mixed_language | 4 | 100.0% |
| standard | 6 | 100.0% |
| unknown_intent | 6 | 100.0% |

## Failed Cases

### colloquial-01 — colloquial

Input: `How's ORD057 doing?`

Status: `failed`

Failed checks: slots
Error categories: slot_value_error

```json
{
  "expected": {
    "intents": [
      "order_status"
    ],
    "slots": [
      {
        "order_id": "ORD-057"
      }
    ],
    "missing_slots": [
      []
    ]
  },
  "actual": {
    "intents": [
      "order_status"
    ],
    "slots": [
      {
        "order_id": "ORD057"
      }
    ],
    "missing_slots": [
      []
    ]
  }
}
```

### colloquial-05 — colloquial

Input: `帮我瞅瞅 ORD 8 到哪儿了？`

Status: `failed`

Failed checks: slots
Error categories: slot_value_error

```json
{
  "expected": {
    "intents": [
      "order_status"
    ],
    "slots": [
      {
        "order_id": "ORD-008"
      }
    ],
    "missing_slots": [
      []
    ]
  },
  "actual": {
    "intents": [
      "order_status"
    ],
    "slots": [
      {
        "order_id": "ORD 8"
      }
    ],
    "missing_slots": [
      []
    ]
  }
}
```

### chinese-05 — chinese

Input: `请看看包装工序是否正常。`

Status: `failed`

Failed checks: slots
Error categories: slot_value_error

```json
{
  "expected": {
    "intents": [
      "production_normality"
    ],
    "slots": [
      {
        "stage": "PACKING"
      }
    ],
    "missing_slots": [
      []
    ]
  },
  "actual": {
    "intents": [
      "production_normality"
    ],
    "slots": [
      {
        "stage": "PACKAGING"
      }
    ],
    "missing_slots": [
      []
    ]
  }
}
```
