#!/usr/bin/env bash
# Copia del database adesso, oltre a quelle automatiche (es. prima di un aggiornamento).
# Finisce in backup/, con lo stesso formato dei backup automatici: si ripristina con ripristina.sh.
source "$(dirname "$0")/_comune.sh"

NOME="backup/$(progetto)-manuale-$(date +%Y%m%d-%H%M%S).sql.gz"
backup_database "$NOME"
echo "Backup salvato: $NOME ($(du -h "$NOME" | cut -f1))"
