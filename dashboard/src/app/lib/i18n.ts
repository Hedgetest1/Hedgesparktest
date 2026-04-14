/**
 * i18n.ts — Phase Ω''' lightweight translation helper.
 *
 * Zero dependencies. **English is the default for every new session**:
 * we do NOT read navigator.language. Locale only changes if the merchant
 * explicitly picks one via Settings, and the choice persists in
 * localStorage. Rationale: the source of truth for copy is EN; translations
 * are opt-in, never auto-applied based on the browser locale (which
 * produces drift when the merchant's Shopify admin is configured in
 * a different language than their browser).
 *
 * Supported languages: en, it, es, fr, de.
 *
 * Usage:
 *   import { t, setLocale, getLocale } from "@/app/lib/i18n";
 *   t("hero.headline_1")
 */

export type Locale = "en" | "it" | "es" | "fr" | "de";

const SUPPORTED: Locale[] = ["en", "it", "es", "fr", "de"];
const STORAGE_KEY = "hs_locale";

type Dict = Record<string, string>;

const TRANSLATIONS: Record<Locale, Dict> = {
  en: {
    "hero.eyebrow_1": "Shopify App",
    "hero.eyebrow_2": "AI Revenue Intelligence",
    "hero.headline_1": "Your store is leaking money.",
    "hero.headline_2": "You don't know why.",
    "hero.headline_3": "We show you where.",
    "hero.sub": "The AI revenue leak detector built for Shopify. Finds products that get attention but don't sell. Stops the bleed. Proves the recovery.",
    "hero.cta_primary": "Install on Shopify",
    "hero.cta_secondary": "See how it works",
    "hero.cta_disclaimer": "Installs in 30 seconds. Tracking starts on the next visitor.",
    "demo.eyebrow": "See your numbers in 30 seconds",
    "demo.title": "No install. No OAuth. Just your Shopify URL.",
    "demo.sub": "We scan your public catalog and show you a real revenue estimate before you sign up.",
    "demo.placeholder": "yourstore.myshopify.com",
    "demo.button": "Run preview",
    "demo.button_loading": "Scanning…",
    "ask.eyebrow": "Ask Hedge Spark",
    "ask.title": "Ask any question about your store",
    "ask.sub": "Plain language, instant answer. No charts to dig through.",
    "ask.placeholder": "Why did revenue drop yesterday?",
    "ask.button": "Ask",
    "why.eyebrow": "The Why Engine",
    "why.title": "What's actually driving the numbers",
    "why.healthy": "All quiet — no causal anomalies detected.",
    "why.next_step": "Next step",
    "anomaly.eyebrow": "Anomaly Radar",
    "anomaly.title": "Cross-signal fusion alerts",
    "anomaly.healthy": "No correlated anomalies right now.",
    "common.confidence": "confident",
    "common.loading": "Loading…",
    "common.connect": "Connect",
    "common.connected": "connected",
    "common.not_connected": "not connected",
  },
  it: {
    "hero.eyebrow_1": "App Shopify",
    "hero.eyebrow_2": "AI Revenue Intelligence",
    "hero.headline_1": "Il tuo store sta perdendo soldi.",
    "hero.headline_2": "Non sai perché.",
    "hero.headline_3": "Te lo mostriamo noi.",
    "hero.sub": "Il rilevatore AI di leak di fatturato per Shopify. Trova i prodotti che ricevono attenzione ma non vendono. Ferma l'emorragia. Prova il recupero.",
    "hero.cta_primary": "Installa su Shopify",
    "hero.cta_secondary": "Scopri come funziona",
    "hero.cta_disclaimer": "Installazione in 30 secondi. Il tracking parte dal prossimo visitatore.",
    "demo.eyebrow": "Vedi i tuoi numeri in 30 secondi",
    "demo.title": "Niente installazione. Niente OAuth. Basta il tuo URL Shopify.",
    "demo.sub": "Scansioniamo il tuo catalogo pubblico e ti mostriamo una stima reale di fatturato prima ancora di registrarti.",
    "demo.placeholder": "tuostore.myshopify.com",
    "demo.button": "Avvia anteprima",
    "demo.button_loading": "Scansione…",
    "ask.eyebrow": "Chiedi a Hedge Spark",
    "ask.title": "Fai qualsiasi domanda sul tuo store",
    "ask.sub": "Linguaggio naturale, risposta istantanea. Nessun grafico in cui scavare.",
    "ask.placeholder": "Perché ieri il fatturato è calato?",
    "ask.button": "Chiedi",
    "why.eyebrow": "Il Motore del Perché",
    "why.title": "Cosa sta davvero guidando i numeri",
    "why.healthy": "Tutto tranquillo — nessuna anomalia causale rilevata.",
    "why.next_step": "Prossimo passo",
    "anomaly.eyebrow": "Radar Anomalie",
    "anomaly.title": "Allarmi fusione cross-signal",
    "anomaly.healthy": "Nessuna anomalia correlata al momento.",
    "common.confidence": "di confidenza",
    "common.loading": "Caricamento…",
    "common.connect": "Connetti",
    "common.connected": "connesso",
    "common.not_connected": "non connesso",
  },
  es: {
    "hero.eyebrow_1": "App Shopify",
    "hero.eyebrow_2": "Inteligencia de Ingresos AI",
    "hero.headline_1": "Tu tienda está perdiendo dinero.",
    "hero.headline_2": "No sabes por qué.",
    "hero.headline_3": "Nosotros te mostramos dónde.",
    "hero.sub": "El detector AI de fugas de ingresos para Shopify. Encuentra productos que reciben atención pero no venden. Detiene la hemorragia. Prueba la recuperación.",
    "hero.cta_primary": "Instalar en Shopify",
    "hero.cta_secondary": "Ver cómo funciona",
    "hero.cta_disclaimer": "Se instala en 30 segundos. El tracking comienza con el próximo visitante.",
    "demo.eyebrow": "Ve tus números en 30 segundos",
    "demo.title": "Sin instalación. Sin OAuth. Solo tu URL de Shopify.",
    "demo.sub": "Escaneamos tu catálogo público y te mostramos una estimación real antes de registrarte.",
    "demo.placeholder": "tutienda.myshopify.com",
    "demo.button": "Iniciar vista previa",
    "demo.button_loading": "Escaneando…",
    "ask.eyebrow": "Pregunta a Hedge Spark",
    "ask.title": "Haz cualquier pregunta sobre tu tienda",
    "ask.sub": "Lenguaje natural, respuesta instantánea. Sin gráficos donde escarbar.",
    "ask.placeholder": "¿Por qué bajaron las ventas ayer?",
    "ask.button": "Preguntar",
    "why.eyebrow": "El Motor del Porqué",
    "why.title": "Qué está realmente moviendo los números",
    "why.healthy": "Todo tranquilo — sin anomalías causales detectadas.",
    "why.next_step": "Próximo paso",
    "anomaly.eyebrow": "Radar de Anomalías",
    "anomaly.title": "Alertas de fusión multi-señal",
    "anomaly.healthy": "Sin anomalías correlacionadas en este momento.",
    "common.confidence": "de confianza",
    "common.loading": "Cargando…",
    "common.connect": "Conectar",
    "common.connected": "conectado",
    "common.not_connected": "no conectado",
  },
  fr: {
    "hero.eyebrow_1": "App Shopify",
    "hero.eyebrow_2": "Intelligence Revenue IA",
    "hero.headline_1": "Votre boutique perd de l'argent.",
    "hero.headline_2": "Vous ne savez pas pourquoi.",
    "hero.headline_3": "Nous vous montrons où.",
    "hero.sub": "Le détecteur IA de fuites de revenus pour Shopify. Trouve les produits qui attirent l'attention mais ne vendent pas. Arrête l'hémorragie. Prouve la récupération.",
    "hero.cta_primary": "Installer sur Shopify",
    "hero.cta_secondary": "Voir comment ça marche",
    "hero.cta_disclaimer": "Installation en 30 secondes. Le tracking démarre dès le prochain visiteur.",
    "demo.eyebrow": "Voyez vos chiffres en 30 secondes",
    "demo.title": "Pas d'installation. Pas d'OAuth. Juste votre URL Shopify.",
    "demo.sub": "Nous scannons votre catalogue public et vous montrons une estimation réelle avant l'inscription.",
    "demo.placeholder": "votreboutique.myshopify.com",
    "demo.button": "Lancer l'aperçu",
    "demo.button_loading": "Analyse…",
    "ask.eyebrow": "Demandez à Hedge Spark",
    "ask.title": "Posez n'importe quelle question sur votre boutique",
    "ask.sub": "Langage naturel, réponse instantanée. Aucun graphique à fouiller.",
    "ask.placeholder": "Pourquoi les ventes ont-elles chuté hier ?",
    "ask.button": "Demander",
    "why.eyebrow": "Le Moteur du Pourquoi",
    "why.title": "Ce qui pilote vraiment les chiffres",
    "why.healthy": "Tout est calme — aucune anomalie causale détectée.",
    "why.next_step": "Étape suivante",
    "anomaly.eyebrow": "Radar d'Anomalies",
    "anomaly.title": "Alertes de fusion multi-signaux",
    "anomaly.healthy": "Aucune anomalie corrélée en ce moment.",
    "common.confidence": "de confiance",
    "common.loading": "Chargement…",
    "common.connect": "Connecter",
    "common.connected": "connecté",
    "common.not_connected": "non connecté",
  },
  de: {
    "hero.eyebrow_1": "Shopify-App",
    "hero.eyebrow_2": "KI-Umsatzintelligenz",
    "hero.headline_1": "Ihr Shop verliert Geld.",
    "hero.headline_2": "Sie wissen nicht warum.",
    "hero.headline_3": "Wir zeigen Ihnen wo.",
    "hero.sub": "Der KI-Umsatzlecksucher für Shopify. Findet Produkte, die Aufmerksamkeit erhalten, aber nicht verkaufen. Stoppt das Bluten. Beweist die Erholung.",
    "hero.cta_primary": "Auf Shopify installieren",
    "hero.cta_secondary": "Sehen Sie, wie es funktioniert",
    "hero.cta_disclaimer": "Installation in 30 Sekunden. Tracking startet beim nächsten Besucher.",
    "demo.eyebrow": "Sehen Sie Ihre Zahlen in 30 Sekunden",
    "demo.title": "Keine Installation. Kein OAuth. Nur Ihre Shopify-URL.",
    "demo.sub": "Wir scannen Ihren öffentlichen Katalog und zeigen Ihnen eine echte Schätzung vor der Anmeldung.",
    "demo.placeholder": "ihrshop.myshopify.com",
    "demo.button": "Vorschau starten",
    "demo.button_loading": "Scannen…",
    "ask.eyebrow": "Fragen Sie Hedge Spark",
    "ask.title": "Stellen Sie jede Frage zu Ihrem Shop",
    "ask.sub": "Natürliche Sprache, sofortige Antwort. Keine Diagramme zum Durchwühlen.",
    "ask.placeholder": "Warum sind die Verkäufe gestern gefallen?",
    "ask.button": "Fragen",
    "why.eyebrow": "Die Warum-Engine",
    "why.title": "Was die Zahlen wirklich antreibt",
    "why.healthy": "Alles ruhig — keine kausalen Anomalien erkannt.",
    "why.next_step": "Nächster Schritt",
    "anomaly.eyebrow": "Anomalie-Radar",
    "anomaly.title": "Mehrsignal-Fusion-Alarme",
    "anomaly.healthy": "Derzeit keine korrelierten Anomalien.",
    "common.confidence": "Vertrauen",
    "common.loading": "Wird geladen…",
    "common.connect": "Verbinden",
    "common.connected": "verbunden",
    "common.not_connected": "nicht verbunden",
  },
};

let _currentLocale: Locale = "en";

/**
 * Read the merchant's previously persisted locale choice, if any.
 * EN is the default for every new session — we do NOT inspect
 * navigator.language. The only way to end up on a non-EN locale is an
 * explicit previous `setLocale()` call (Settings UI), persisted in
 * localStorage.
 */
function readStoredLocale(): Locale {
  if (typeof window === "undefined") return "en";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && SUPPORTED.includes(stored as Locale)) return stored as Locale;
  } catch {}
  return "en";
}

if (typeof window !== "undefined") {
  _currentLocale = readStoredLocale();
}

export function getLocale(): Locale {
  return _currentLocale;
}

export function setLocale(locale: Locale): void {
  if (!SUPPORTED.includes(locale)) return;
  _currentLocale = locale;
  if (typeof window !== "undefined") {
    try { window.localStorage.setItem(STORAGE_KEY, locale); } catch {}
  }
}

export function t(key: string, fallback?: string): string {
  const dict = TRANSLATIONS[_currentLocale] || TRANSLATIONS.en;
  return dict[key] || TRANSLATIONS.en[key] || fallback || key;
}

export function supportedLocales(): Locale[] {
  return [...SUPPORTED];
}
