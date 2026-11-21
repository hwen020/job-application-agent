"""Public adapter has no private applicant storage."""


class RunnerError(ValueError):
    pass


def private_disabled(*args):
    raise RunnerError('Private applicant storage is disabled in Synthetic Demo.')


def current_profile(db):
    return {}


def references(db, job):
    return {}


private_read = private_write = private_disabled
