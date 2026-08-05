package it.lysauto.workflowhub;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * Dice a JS se questo è il primo caricamento di pagina da quando il
 * PROCESSO Android è partito — a differenza di sessionStorage (che il
 * WebView non garantisce di azzerare esattamente e solo alla vera morte
 * del processo), uno static field Java viene ricreato una volta sola per
 * avvio reale del processo: una navigazione interna (link cliccato,
 * pagina ricaricata nella stessa WebView, stessa Activity) non tocca mai
 * questa classe già caricata in memoria, solo un riavvio vero del
 * processo la ricarica da zero con coldStart di nuovo a true.
 *
 * consume() consuma il flag: la prima chiamata in un processo restituisce
 * true, tutte le successive false, finché il processo non viene
 * ricreato.
 */
@CapacitorPlugin(name = "ColdStart")
public class ColdStartPlugin extends Plugin {
    private static boolean coldStart = true;

    @PluginMethod
    public void consume(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("wasColdStart", coldStart);
        coldStart = false;
        call.resolve(ret);
    }
}
