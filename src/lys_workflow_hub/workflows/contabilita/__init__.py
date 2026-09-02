"""Contabilità gestionale interna + fatturazione elettronica SDI.

Livello di contabilità analitica (ricavi/costi per pratica e per categoria),
NON contabilità fiscale: nessuna partita doppia, nessun registro IVA, nessun
bilancio. Non sostituisce il software del commercialista.

Repository (modello dati) in ``core/contabilita_*_repository.py``.
Routes in ``web/routes_contabilita.py``.
Logica di dominio (scheda economica pratica, mapping fattura→movimento) qui.
"""
