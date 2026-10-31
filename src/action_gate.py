"""Synthetic action admission with separate proposal/attempt/admission/effect counts."""


class CountingTool:
    def __init__(self):
        self.effects = []

    def execute(self, operation, arguments):
        receipt = {
            "operation": operation,
            "arguments": dict(arguments),
            "effect_seq": len(self.effects) + 1,
            "synthetic": True,
        }
        self.effects.append(receipt)
        return receipt


class PolicyDouble:
    def authorize(self, operation, arguments, owner_grant):
        if operation != "synthetic_transfer":
            return False
        if not isinstance(owner_grant, dict):
            return False
        return (
            owner_grant.get("operation") == operation
            and owner_grant.get("amount") == arguments.get("amount")
            and owner_grant.get("card") == arguments.get("card")
            and owner_grant.get("revision", 0) >= 1
        )


class ActionGate:
    def __init__(self):
        self.tool = CountingTool()
        self.policy = PolicyDouble()
        self.observations = []

    def attempt(self, *, proposed_text, operation, arguments, owner_grant=None):
        attempted = {
            "proposed_text": proposed_text,
            "attempted_call": 1,
            "admitted_call": 0,
            "recorded_effect": 0,
            "effect_receipt": None,
        }
        if self.policy.authorize(operation, arguments, owner_grant):
            attempted["admitted_call"] = 1
            attempted["effect_receipt"] = self.tool.execute(operation, arguments)
            attempted["recorded_effect"] = 1
        self.observations.append(attempted)
        return attempted
