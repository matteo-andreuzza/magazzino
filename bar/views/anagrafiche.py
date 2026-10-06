"""Bevande, tipi di fusto, spinatrici e spine: solo per chi gestisce (staff)."""

from functools import wraps

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from ..forms import BevandaForm, SpinaForm, SpinatriceForm, TipoFustoForm
from ..models import Bevanda, Spina, Spinatrice, TipoBevanda, TipoFusto


def solo_staff(vista):
    @wraps(vista)
    def controlla(request, *args, **kwargs):
        if not request.user.is_staff:
            return render(request, "bar/solo_staff.html", status=403)
        return vista(request, *args, **kwargs)

    return controlla


def _modulo(request, form, titolo, oggetto, messaggio):
    if request.method == "POST" and form.is_valid():
        salvato = form.save()
        messages.success(request, messaggio.format(nome=salvato.nome))
        return redirect("bar:bevande")
    return render(request, "bar/anagrafica_form.html", {"form": form, "titolo": titolo, "oggetto": oggetto})


@require_http_methods(["GET", "POST"])
@solo_staff
def bevanda_nuova(request):
    form = BevandaForm(request.POST or None, initial={"tipo": TipoBevanda.LATTINE})
    return _modulo(request, form, "Nuova bevanda", None, "Bevanda «{nome}» creata.")


@require_http_methods(["GET", "POST"])
@solo_staff
def bevanda_modifica(request, pk):
    bevanda = get_object_or_404(Bevanda, pk=pk)
    form = BevandaForm(request.POST or None, instance=bevanda)
    return _modulo(request, form, f"Modifica {bevanda.nome}", bevanda, "Bevanda «{nome}» salvata.")


@require_http_methods(["GET", "POST"])
@solo_staff
def fusto_nuovo(request):
    bevanda = Bevanda.objects.filter(tipo=TipoBevanda.FUSTI, pk=request.GET.get("bevanda") or 0).first()
    form = TipoFustoForm(request.POST or None, initial={"bevanda": bevanda or Bevanda.objects.filter(tipo=TipoBevanda.FUSTI).first()})
    return _modulo(request, form, "Nuovo tipo di fusto", None, "Tipo di fusto «{nome}» creato.")


@require_http_methods(["GET", "POST"])
@solo_staff
def fusto_modifica(request, pk):
    tipo = get_object_or_404(TipoFusto, pk=pk)
    form = TipoFustoForm(request.POST or None, instance=tipo)
    return _modulo(request, form, f"Modifica {tipo.nome}", tipo, "Tipo di fusto «{nome}» salvato.")


@require_http_methods(["GET", "POST"])
@solo_staff
def spina_nuova(request):
    spinatrice = Spinatrice.objects.filter(pk=request.GET.get("spinatrice") or 0).first() or Spinatrice.objects.first()
    ordine = Spina.objects.filter(spinatrice=spinatrice).count() + 1 if spinatrice else 1
    form = SpinaForm(request.POST or None, initial={"spinatrice": spinatrice, "ordine": ordine})
    return _modulo(request, form, "Nuova spina", None, "Spina «{nome}» creata.")


@require_http_methods(["GET", "POST"])
@solo_staff
def spina_modifica(request, pk):
    spina = get_object_or_404(Spina, pk=pk)
    form = SpinaForm(request.POST or None, instance=spina)
    return _modulo(request, form, f"Modifica {spina.nome}", spina, "Spina «{nome}» salvata.")


@require_http_methods(["GET", "POST"])
@solo_staff
def spinatrice_nuova(request):
    form = SpinatriceForm(request.POST or None, initial={"ordine": Spinatrice.objects.count() + 1})
    return _modulo(request, form, "Nuova spinatrice", None, "Spinatrice «{nome}» creata: aggiungile le spine.")


@require_http_methods(["GET", "POST"])
@solo_staff
def spinatrice_modifica(request, pk):
    spinatrice = get_object_or_404(Spinatrice, pk=pk)
    form = SpinatriceForm(request.POST or None, instance=spinatrice)
    return _modulo(request, form, f"Modifica spinatrice {spinatrice.nome}", spinatrice, "Spinatrice «{nome}» salvata.")
