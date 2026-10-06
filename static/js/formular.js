/* Form helpers of the design system, vendored into this repository: password toggle and
   protection against double submission. Not written for LabCIRS; covered by the AGPL of
   this repository. The print button at the end was added for LabCIRS. */
/* Formular-Komfort -- ausschliesslich Verbesserungen, nie Voraussetzung.
 *
 * Grundsatz: ohne JavaScript muss jedes Formular vollstaendig bedienbar bleiben. Deshalb
 * werden Schaltflaechen, die nur mit JavaScript sinnvoll sind, serverseitig `hidden`
 * ausgeliefert und hier erst eingeblendet -- ein Nutzer ohne JavaScript sieht keine tote
 * Schaltflaeche.
 *
 * Ausgelagert aus den Templates (frueher Inline-Handler), damit die CSP ohne
 * `script-src 'unsafe-inline'` auskommt.
 */
(function () {
  "use strict";

  /* --- Passwort ein-/ausblenden --------------------------------------------------------- */
  document.querySelectorAll("[data-pw-toggle]").forEach(function (schalter) {
    var feld = document.getElementById(schalter.getAttribute("data-pw-toggle"));
    if (!feld) return;

    schalter.hidden = false;
    schalter.setAttribute("aria-controls", feld.id);
    schalter.setAttribute("aria-pressed", "false");

    schalter.addEventListener("click", function () {
      var sichtbar = feld.type === "text";
      feld.type = sichtbar ? "password" : "text";
      schalter.textContent = sichtbar ? "Anzeigen" : "Verbergen";
      schalter.setAttribute("aria-pressed", String(!sichtbar));
      /* Der Fokus bleibt beim Nutzer: nach dem Umschalten weiter im Passwortfeld, damit die
         Tastaturbedienung nicht abreisst. */
      feld.focus();
      var ende = feld.value.length;
      try { feld.setSelectionRange(ende, ende); } catch (e) { /* type=password erlaubt das nicht ueberall */ }
    });
  });

  /* --- Schutz vor Doppelabsenden --------------------------------------------------------
   * Ein zweites POST kann einen zweiten Anmeldeversuch, ein zweites Dokument oder einen
   * zweiten Statuswechsel ausloesen. Der Schutz ist hier nur die sichtbare Haelfte -- die
   * verbindliche liegt serverseitig.
   */
  document.querySelectorAll("form[data-einmal-absenden]").forEach(function (formular) {
    formular.addEventListener("submit", function () {
      var knopf = formular.querySelector('button[type="submit"], button:not([type])');
      if (!knopf || knopf.dataset.laeuft === "1") return;
      knopf.dataset.laeuft = "1";

      var text = knopf.getAttribute("data-busy-text");
      if (text) {
        /* Animation nie allein: der Text sagt, was passiert. */
        knopf.innerHTML =
          '<span class="ui-spinner" aria-hidden="true"></span><span>' + text + "</span>";
      }
      knopf.setAttribute("aria-disabled", "true");
      knopf.setAttribute("aria-busy", "true");
      /* `disabled` wuerde den Knopfwert nicht mitsenden -- `aria-disabled` plus
         Klick-Unterdrueckung erhaelt das Absenden und verhindert trotzdem den zweiten Klick. */
      knopf.addEventListener("click", function (ereignis) {
        if (knopf.dataset.laeuft === "1") ereignis.preventDefault();
      });
    });
  });

  /* --- Print button (added for LabCIRS) -------------------------------------------------
   * The print view of the evaluations has a button that opens the print dialog of the browser.
   * It is served hidden: without a script it would be a dead button, and the page tells the way
   * with the keyboard (Ctrl+P) instead, which is taken away when the button works.
   */
  if (typeof window.print === "function") {
    document.querySelectorAll("[data-drucken]").forEach(function (knopf) {
      knopf.hidden = false;
      knopf.addEventListener("click", function () { window.print(); });
    });
    document.querySelectorAll("[data-drucken-hinweis]").forEach(function (hinweis) {
      hinweis.hidden = true;
    });
  }
})();
