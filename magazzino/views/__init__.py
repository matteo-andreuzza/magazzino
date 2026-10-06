from .allegati import allegato_elimina, allegato_file, operazione_allegati, prodotto_foto, prodotto_miniatura  # noqa: F401
from .anagrafiche import (  # noqa: F401
    prodotto_modifica,
    prodotto_nuovo,
    servizio_chiudi,
    servizio_elimina,
    servizio_modifica,
    servizio_nuovo,
    servizio_riapri,
)
from .esporta import giacenze_csv, movimenti_csv, riepilogo_csv  # noqa: F401
from .moduli import (  # noqa: F401
    carico,
    movimento_annulla,
    operazione_annulla,
    rettifica,
    scarto,
    servizio_cucina,
    servizio_reso,
    spostamento,
)
from .pagine import (  # noqa: F401
    carico_scelta,
    giacenze,
    home,
    menu,
    operazione_dettaglio,
    operazioni,
    prodotti,
    prodotto_dettaglio,
    rettifica_scelta,
    riepilogo,
    scarto_scelta,
    servizi,
    servizio_dettaglio,
    spostamento_destinazione,
    spostamento_scelta,
    ubicazione_dettaglio,
    ubicazioni,
)
from .guida import guida, salute  # noqa: F401
