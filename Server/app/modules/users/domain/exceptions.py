class UserAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InactiveUserError(Exception):
    pass


class IncorrectPasswordError(Exception):
    pass


class AdministratorNotConfiguredError(Exception):
    pass
