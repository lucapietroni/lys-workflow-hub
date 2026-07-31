package it.lysauto.workflowhub;

import android.os.Bundle;
import android.view.WindowManager;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Va registrato PRIMA di super.onCreate() (che inizializza il
        // bridge/WebView) — vedi ColdStartPlugin.java.
        registerPlugin(ColdStartPlugin.class);
        super.onCreate(savedInstanceState);
        // Impedisce screenshot/registrazione schermo e nasconde l'anteprima
        // nel task-switcher (recent apps): l'app mostra dati cliente/foto
        // pratiche indipendentemente dal toggle di sblocco biometrico
        // (self-service, opt-in), quindi questa protezione resta sempre
        // attiva. Segnalato in review come gap sullo scenario dichiarato
        // "telefono sbloccato lasciato incustodito" — lo sblocco biometrico
        // da solo copre solo il resume dell'app, non l'anteprima catturata
        // da Android nel momento in cui l'app va in background.
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
    }
}
