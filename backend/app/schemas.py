REVIEW_SCHEMA = {
  "name": "contract_review_result",
  "schema": {
    "type": "object",
    "properties": {
      "items": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "position": {"type": "string"},
            "action": {"type": "string"},
            "revised_text": {"type": "string"},
            "bold_spans": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "start": {"type": "integer"},
                  "end": {"type": "integer"}
                },
                "required": ["start", "end"]
              }
            },
            "risk": {"type": "string"},
            "reason": {"type": "string"},
            "consequence": {"type": "string"}
          },
          "required": [
            "position",
            "action",
            "revised_text",
            "bold_spans",
            "risk",
            "reason",
            "consequence"
          ]
        }
      }
    },
    "required": ["items"]
  }
}