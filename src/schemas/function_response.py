class FunctionResponse:
    def __init__(self, status: bool, data = None, error = None):
        self.status = status
        self.data = data
        self.error = error

    def is_successful(self) -> bool:
        return self.status

    def is_error(self) -> bool:
        return not self.status

    def get_data(self):
        return self.data

    def get_error_message(self):
        return self.error

    def __str__(self):
        return f"FunctionResponse(status={self.status}, data={self.data}, error={self.error})"

    def __repr__(self):
        return self.__str__()