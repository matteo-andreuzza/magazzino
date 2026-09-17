#!/usr/bin/env python
import os
import sys


def avvia_debugger():
    """Con DEBUGPY_PORTA impostata apre la porta per il debugger di VS Code.

    Con la ricarica automatica runserver lancia un processo figlio che esegue
    l'app e ripartirà a ogni modifica: il debugger va aperto lì (RUN_MAIN),
    non nel processo che sorveglia i file.
    """
    porta = os.environ.get("DEBUGPY_PORTA")
    # Solo per il server: non per migrate e gli altri comandi lanciati all'avvio.
    if not porta or "runserver" not in sys.argv:
        return
    if "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
        return
    import debugpy

    debugpy.listen(("0.0.0.0", int(porta)))
    print(f"Debugger in ascolto sulla porta {porta}", flush=True)


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    avvia_debugger()
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
