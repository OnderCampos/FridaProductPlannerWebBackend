from src.schemas.response import ResponseModel


def test_response_model_dict_roundtrip():
    model = ResponseModel(success=True, message="ok", data={"value": 1})
    payload = model.dict()
    assert payload == {"success": True, "message": "ok", "data": {"value": 1}}
