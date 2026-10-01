"""Admin commands, run inside the backend container:

    docker compose exec backend python -m app.cli create-admin <username>
    docker compose exec backend python -m app.cli reset-password <username>

The password is prompted twice (not echoed), or taken from the
PPPOE_ADMIN_PASSWORD environment variable for scripted use.
"""
import argparse
import getpass
import os
import sys

from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.database import SessionLocal
from app.models.user import User

PASSWORD_ENV_VAR = "PPPOE_ADMIN_PASSWORD"


class CliError(Exception):
    pass


def _read_password() -> str:
    password = os.environ.get(PASSWORD_ENV_VAR)
    if password is None:
        password = getpass.getpass("Contraseña: ")
        if getpass.getpass("Repetir contraseña: ") != password:
            raise CliError("Las contraseñas no coinciden.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CliError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    return password


def _run(command: str, username: str) -> str:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if command == "create-admin":
            if user is not None:
                raise CliError(
                    f"El usuario {username!r} ya existe. Usá reset-password para cambiarle la contraseña."
                )
            db.add(User(username=username, password_hash=hash_password(_read_password())))
            db.commit()
            return f"Usuario {username!r} creado."
        if user is None:
            raise CliError(f"El usuario {username!r} no existe.")
        user.password_hash = hash_password(_read_password())
        db.commit()
        return f"Contraseña de {username!r} actualizada."
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administración de usuarios")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create-admin", help="Crear un usuario admin").add_argument("username")
    commands.add_parser("reset-password", help="Cambiar la contraseña de un usuario").add_argument("username")
    args = parser.parse_args(argv)

    try:
        print(_run(args.command, args.username))
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
