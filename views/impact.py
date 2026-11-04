"""Analyse d'impact d'un changement (module impact_page de l'équipe)."""
import impact_page
import shared as sh

_c = sh.ctx()
impact_page.render(_c["m"], _c["comp"], _c["mat"])
