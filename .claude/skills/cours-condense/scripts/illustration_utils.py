"""
Utilitaires a importer dans chaque script d'illustration genere pour le
cours (etape 2 / prompt 1-B). Implemente les consignes impératives
"Anti-chevauchement et dimensionnement" :

- fig.canvas.draw() puis get_window_extent(renderer) sur chaque element
- aucune paire d'elements ne doit se chevaucher, aucun texte ne doit
  depasser sa boite englobante
- marge minimale de 0.03 (coordonnees de figure) entre deux elements
- figsize calcule a partir du nombre d'elements et de la longueur du texte
  le plus long, jamais une valeur fixe par defaut

Usage type dans un script d'illustration :

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from illustration_utils import suggested_figsize, verify_no_overlap

    textes = ["Entree", "Couche cachee (ReLU)", "Sortie"]
    figsize = suggested_figsize(nb_elements=len(textes), max_text_len=max(len(t) for t in textes))

    for tentative in range(5):
        fig, ax = plt.subplots(figsize=figsize)
        artistes = []
        # ... positionner boites/texte/fleches, ajouter chaque Artist a `artistes` ...
        ok, problemes = verify_no_overlap(fig, artistes)
        if ok:
            break
        figsize = (figsize[0] * 1.15, figsize[1] * 1.15)
        plt.close(fig)
    else:
        raise RuntimeError(f"Chevauchement persistant apres plusieurs tentatives: {problemes}")

    fig.savefig("sortie.png", dpi=300)
    plt.close(fig)
"""

MARGE_MIN = 0.03  # fraction de la largeur/hauteur totale de la figure

# Espacement bord-a-bord minimal entre deux boites reliees par connect().
# Derive du calcul interne de connect()/_shrink_segment : la fleche est
# raccourcie de min(margin, distance*0.45) a chaque bout, donc pour que la
# marge residuelle respecte MARGE_MIN il faut distance*0.45 >= MARGE_MIN,
# soit distance >= MARGE_MIN/0.45 (~0.0667). On ajoute une marge de securite
# (BOX_RENDER_OVERSHOOT) car un FancyBboxPatch se rend toujours un peu plus
# grand que les width/height demandes (pad + demi-epaisseur du trait du
# boxstyle "round"), ce qui grignote une partie de cette marge sans que
# _box_extent (qui ne stocke que les valeurs nominales) ne le sache. Sans ce
# supplement, des cas limites mesures empiriquement lors du stress-test de
# ce module (ex. 4 boites en rangee avec un espacement nominal de 0.08)
# echouaient quand meme de quelques millimes malgre un espacement nominal
# theoriquement suffisant.
BOX_RENDER_OVERSHOOT = 0.02
ESPACEMENT_MIN_BOITES = round(MARGE_MIN / 0.45 + BOX_RENDER_OVERSHOOT, 4)  # ~0.087


def suggested_figsize(nb_elements, max_text_len, base_w=3.0, base_h=1.2,
                       w_per_element=1.1, h_per_element=0.55, char_w=0.09):
    """Point de depart raisonnable, jamais une constante figee : le script
    appelant doit quand meme reverifier via verify_no_overlap() et
    agrandir si necessaire."""
    largeur = base_w + nb_elements * w_per_element + max_text_len * char_w
    hauteur = base_h + nb_elements * h_per_element
    return (round(largeur, 2), round(hauteur, 2))


def _bbox_figure_fraction(artist, fig, renderer):
    bbox_px = artist.get_window_extent(renderer=renderer)
    return bbox_px.transformed(fig.transFigure.inverted())


def _overlap_with_margin(b1, b2, marge):
    """Vrai si les deux boites (etendues de marge/2 chacune) se chevauchent."""
    b1x0, b1x1 = b1.x0 - marge / 2, b1.x1 + marge / 2
    b1y0, b1y1 = b1.y0 - marge / 2, b1.y1 + marge / 2
    b2x0, b2x1 = b2.x0 - marge / 2, b2.x1 + marge / 2
    b2y0, b2y1 = b2.y0 - marge / 2, b2.y1 + marge / 2
    return not (b1x1 < b2x0 or b2x1 < b1x0 or b1y1 < b2y0 or b2y1 < b1y0)


def verify_no_overlap(fig, artistes, marge=MARGE_MIN, ignore_pairs=None):
    """
    artistes: liste d'objets matplotlib (Text, Rectangle, FancyArrow, ...)
    ignore_pairs: ensemble optionnel de paires d'indices (i,j) a ne pas
        signaler (ex. une boite et le texte qu'elle contient volontairement).
    Retourne (ok: bool, problemes: list[str]).
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    ignore_pairs = ignore_pairs or set()
    ignore_pairs = {tuple(sorted(p)) for p in ignore_pairs}

    boites = []
    for i, art in enumerate(artistes):
        try:
            bbox = _bbox_figure_fraction(art, fig, renderer)
        except Exception:
            continue
        boites.append((i, art, bbox))

    problemes = []

    # aucun element ne doit depasser les bords de la figure [0,1]x[0,1]
    for i, art, bbox in boites:
        if bbox.x0 < 0 or bbox.x1 > 1 or bbox.y0 < 0 or bbox.y1 > 1:
            problemes.append(f"element #{i} ({art!r}) depasse les bords de la figure")

    # aucune paire ne doit se chevaucher (marge minimale respectee)
    # exceptions :
    # - deux fleches entre elles peuvent se croiser (convention usuelle de
    #   schema en eventail/flowchart) sans nuire a la lisibilite - leurs
    #   boites englobantes rectangulaires se recoupent presque toujours pres
    #   d'un point de convergence commun, meme quand les traits eux-memes
    #   restent parfaitement lisibles.
    # - deux courbes (Line2D, ex. graphiques a plusieurs series via
    #   DiagramBuilder.line()) partagent quasi systematiquement la meme
    #   plage d'abscisses/ordonnees : leur boite englobante rectangulaire se
    #   recoupe presque toujours des qu'il y a 2+ series sur le meme graphe,
    #   independamment du fait que les traits se croisent reellement ou non.
    #   Verifier leur chevauchement via une AABB grossiere est donc non
    #   pertinent pour des courbes (a la difference d'une boite pleine) et
    #   bloquait a tort la quasi-totalite des graphiques multi-series lors
    #   du stress-test de ce module.
    # Seuls les chevauchements impliquant une boite ou un texte restent
    # signales.
    from matplotlib.patches import FancyArrowPatch
    from matplotlib.lines import Line2D

    for a in range(len(boites)):
        for b in range(a + 1, len(boites)):
            i1, art1, b1 = boites[a]
            i2, art2, b2 = boites[b]
            if (i1, i2) in ignore_pairs or (i2, i1) in ignore_pairs:
                continue
            if isinstance(art1, FancyArrowPatch) and isinstance(art2, FancyArrowPatch):
                continue
            if isinstance(art1, Line2D) and isinstance(art2, Line2D):
                continue
            if _overlap_with_margin(b1, b2, marge):
                problemes.append(f"chevauchement entre element #{i1} ({art1!r}) et #{i2} ({art2!r})")

    return (len(problemes) == 0, problemes)


# ---------------------------------------------------------------------------
# Charte graphique et petites aides de dessin (boites, fleches, texte).
# Ne remplacent pas la verification anti-chevauchement : chaque script doit
# quand meme appeler verify_no_overlap() avant d'enregistrer le PNG.
# ---------------------------------------------------------------------------

BLEU = "#1a365d"
ORANGE = "#c25e00"

import textwrap as _textwrap


def wrap(text, width=22):
    """Reformate chaque ligne a la largeur donnee, MAIS respecte les sauts de
    ligne deja presents dans `text` (y compris les lignes vides utilisees
    comme separateurs) au lieu de les fusionner comme le ferait
    textwrap.wrap() applique directement sur tout le texte."""
    lignes_out = []
    for ligne in text.split("\n"):
        if ligne.strip() == "":
            lignes_out.append("")
        else:
            lignes_out.extend(_textwrap.wrap(ligne, width=width) or [""])
    return "\n".join(lignes_out)


def draw_box(ax, xy, width, height, text, facecolor="white", edgecolor=BLEU,
             textcolor=BLEU, fontsize=10, wrap_width=22, bold=False, zorder=2):
    """Dessine un rectangle arrondi centre en xy avec un texte a l'interieur.
    Retourne (patch_rectangle, texte) — a ajouter aux deux dans la liste
    d'artistes verifiee par verify_no_overlap."""
    from matplotlib.patches import FancyBboxPatch

    x, y = xy
    box = FancyBboxPatch(
        (x - width / 2, y - height / 2), width, height,
        boxstyle="round,pad=0.006,rounding_size=0.012",
        linewidth=1.6, edgecolor=edgecolor, facecolor=facecolor, zorder=zorder,
    )
    ax.add_patch(box)
    txt = ax.text(
        x, y, wrap(text, wrap_width), ha="center", va="center",
        fontsize=fontsize, color=textcolor,
        fontweight="bold" if bold else "normal", zorder=zorder + 1,
    )
    return box, txt


def draw_arrow(ax, start, end, color=BLEU, style="-|>", lw=1.8,
               connectionstyle="arc3,rad=0.0", zorder=1):
    """Dessine une fleche entre deux points (coordonnees axes 0..1).
    Retourne le patch — a ajouter a la liste d'artistes."""
    from matplotlib.patches import FancyArrowPatch

    arrow = FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=14,
        color=color, linewidth=lw, connectionstyle=connectionstyle,
        zorder=zorder, shrinkA=0, shrinkB=0,
    )
    ax.add_patch(arrow)
    return arrow


def new_diagram_axes(figsize):
    """Cree une figure/ax vierge en coordonnees [0,1]x[0,1], sans axes visibles.

    L'axe occupe exactement 100% de la figure (add_axes([0,0,1,1])) : sans
    cela, matplotlib laisse par defaut des marges autour de l'axe (~10-15%
    de chaque cote), et les coordonnees 0..1 utilisees pour positionner les
    boites/fleches ne correspondraient plus aux fractions de figure
    verifiees par verify_no_overlap, faussant tous les calculs de marge."""
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def _shrink_segment(start, end, margin=0.018):
    """Raccourcit un segment aux deux bouts pour laisser un espace avant les
    boites qu'il relie (evite le contact exact qui se lit comme un
    chevauchement une fois la marge minimale appliquee)."""
    import math

    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist == 0:
        return start, end
    # Ne jamais annuler purement et simplement le raccourcissement (un cas
    # limite en virgule flottante pres de dist == 2*margin renverrait alors
    # les points d'origine, colles aux bords des boites). On raccourcit
    # toujours d'au moins un peu, quitte a n'utiliser qu'une fraction de la
    # marge demandee quand le segment est court.
    m = min(margin, dist * 0.45)
    ux, uy = dx / dist, dy / dist
    return (x0 + ux * m, y0 + uy * m), (x1 - ux * m, y1 - uy * m)


class DiagramBuilder:
    """Enveloppe new_diagram_axes/draw_box/draw_arrow/verify_no_overlap pour
    eviter de reecrire la meme mecanique anti-chevauchement dans chaque
    script d'illustration. Usage :

        d = DiagramBuilder(nb_elements=3, max_text_len=20)
        d.box("q", (0.15, 0.5), 0.22, 0.35, "Question utilisateur")
        d.box("r", (0.5, 0.5), 0.22, 0.35, "Recuperation")
        d.arrow((0.15, 0.5), (0.5, 0.5), from_box="q", to_box="r")
        d.save("sortie.png")

    Limites connues (identifiees par stress-test systematique de ce module,
    83 configurations synthetiques couvrant processus en rangee, comparaison
    en colonnes, topologie en etoile, courbes, texte long, libelles de
    fleches ; voir le rapport de session correspondant). Les correctifs
    generaux (exception Line2D/Line2D dans verify_no_overlap, seuil
    ESPACEMENT_MIN_BOITES avec marge de securite, fail-fast dans box() et
    connect()) couvrent la grande majorite des cas, mais deux familles de
    schemas restent a verifier manuellement via .verify()/.save() car elles
    peuvent encore echouer tardivement, sans qu'aucun fail-fast ne le
    detecte a l'avance :

    1. Topologie en etoile a rayon trop faible (un noeud central relie a
       plusieurs noeuds peripheriques rapproches en angle). connect() ne
       verifie que la paire de boites qu'il relie ; il ne sait pas detecter
       qu'une fleche vers un noeud peripherique frole un AUTRE noeud
       peripherique sur son trajet. Mitigation : choisir un rayon assez
       grand pour que les noeuds peripheriques soient nettement separes les
       uns des autres (empiriquement, un rayon domain a partir de ~0.35 sur
       une figure carree passe de facon fiable avec des boites de taille
       usuelle ; en dessous de ~0.30 le risque de chevauchement tardif
       augmente nettement).
    2. Libelle de connect(..., label=...) dans une rangee de boites serrees,
       ou texte tres long dans une boite tres etroite. Le libelle est
       decale perpendiculairement a la fleche (par defaut de 0.09) et peut
       chevaucher une boite voisine sans qu'aucun controle prealable ne le
       detecte ; de meme, un texte de boite qui deborde largement sa boite
       (via un wrap_width trop permissif pour sa largeur) n'est detecte que
       s'il vient toucher un AUTRE element du schema. Mitigation : eviter
       les libelles de fleche quand les boites sont deja proches du seuil
       ESPACEMENT_MIN_BOITES, ou les placer explicitement via label_offset ;
       dimensionner wrap_width et la hauteur de boite en fonction de la
       longueur reelle du texte plutot que par defaut.

    Dans les deux cas, la seule garantie fiable reste d'appeler
    .verify() (ou .save(), qui l'appelle et grossit la figure en boucle)
    et de lire les problemes retournes avant de considerer une illustration
    terminee - les fail-fast ci-dessous reduisent la frequence des echecs
    tardifs, ils ne l'eliminent pas totalement pour ces deux familles.
    """

    def __init__(self, nb_elements, max_text_len, figsize=None, **kwargs):
        self.figsize = figsize or suggested_figsize(nb_elements, max_text_len, **kwargs)
        self.fig, self.ax = new_diagram_axes(self.figsize)
        self.artistes = []
        self._box_extent = {}  # nom -> (x, y, largeur, hauteur)
        self._ignore_pairs = set()

    def box(self, name, xy, width, height, text, **kwargs):
        x, y = xy
        x0, x1, y0, y1 = x - width / 2, x + width / 2, y - height / 2, y + height / 2
        if x0 < 0 or x1 > 1 or y0 < 0 or y1 > 1:
            raise ValueError(
                f"box('{name}', xy={xy}, width={width}, height={height}) : "
                f"cette boite deborderait de la figure (etendue calculee "
                f"x=[{x0:.3f},{x1:.3f}] y=[{y0:.3f},{y1:.3f}], attendu dans "
                f"[0,1]x[0,1]). Reduis sa largeur/hauteur ou rapproche son "
                f"centre du milieu de la figure avant de continuer, plutot "
                f"que de le decouvrir dans verify_no_overlap()."
            )
        # Chevauchement boite-boite direct : non lie a une fleche, donc non
        # couvert par la verification faite dans connect(). Sans ce controle,
        # des boites simplement trop rapprochees (ex. trop nombreuses pour la
        # largeur disponible) ne sont detectees qu'a la fin, dans
        # verify_no_overlap(), une fois toute la figure construite.
        for autre_nom, (ax_, ay_, aw_, ah_) in self._box_extent.items():
            ax0, ax1 = ax_ - aw_ / 2, ax_ + aw_ / 2
            ay0, ay1 = ay_ - ah_ / 2, ay_ + ah_ / 2
            marge = MARGE_MIN + BOX_RENDER_OVERSHOOT
            chevauche = not (
                x1 + marge / 2 < ax0 - marge / 2
                or ax1 + marge / 2 < x0 - marge / 2
                or y1 + marge / 2 < ay0 - marge / 2
                or ay1 + marge / 2 < y0 - marge / 2
            )
            if chevauche:
                raise ValueError(
                    f"box('{name}', xy={xy}, width={width}, height={height}) : "
                    f"chevauche (ou n'a pas assez de marge avec) la boite "
                    f"'{autre_nom}' deja placee en {(ax_, ay_)} de taille "
                    f"({aw_}x{ah_}). Trop de boites pour la largeur/hauteur "
                    f"disponible, ou positions trop rapprochees : reduis leur "
                    f"nombre, leur taille, ou agrandis la figure/l'espacement."
                )
        box_patch, txt = draw_box(self.ax, xy, width, height, text, **kwargs)
        i_box, i_txt = len(self.artistes), len(self.artistes) + 1
        self.artistes.extend([box_patch, txt])
        self._ignore_pairs.add((i_box, i_txt))
        self._box_extent[name] = (xy[0], xy[1], width, height)
        return box_patch, txt

    def arrow(self, start, end, margin=0.05, **kwargs):
        """Le segment est raccourci de `margin` a chaque bout avant d'etre
        trace, ce qui laisse l'espace necessaire vis-a-vis des boites qu'il
        relie sans qu'il faille les nommer explicitement."""
        s, e = _shrink_segment(start, end, margin=margin)
        arrow = draw_arrow(self.ax, s, e, **kwargs)
        self.artistes.append(arrow)
        return arrow

    def text(self, xy, s, **kwargs):
        kwargs.setdefault("ha", "center")
        kwargs.setdefault("va", "center")
        kwargs.setdefault("color", BLEU)
        kwargs.setdefault("fontsize", 10)
        txt = self.ax.text(xy[0], xy[1], s, **kwargs)
        self.artistes.append(txt)
        return txt

    def line(self, xs, ys, color=BLEU, lw=2.0, label=None, marker=None, **kwargs):
        (line_artist,) = self.ax.plot(xs, ys, color=color, lw=lw, label=label,
                                       marker=marker, **kwargs)
        self.artistes.append(line_artist)
        return line_artist

    def verify(self, marge=MARGE_MIN):
        return verify_no_overlap(self.fig, self.artistes, marge=marge,
                                  ignore_pairs=self._ignore_pairs)

    def connect(self, name_from, name_to, margin=0.06, label=None,
                label_offset=None, label_kwargs=None, **arrow_kwargs):
        """Relie deux boites nommees (creees via .box()) par une fleche dont
        les deux extremites sont calculees exactement sur le bord de chaque
        rectangle (intersection rayon/rectangle), quelle que soit leur
        position relative. Evite d'avoir a deviner des coordonnees de bord a
        la main pour chaque diagramme."""
        import math

        x0, y0, w0, h0 = self._box_extent[name_from]
        x1, y1, w1, h1 = self._box_extent[name_to]
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        if dist == 0:
            raise ValueError(f"{name_from} et {name_to} ont le meme centre")
        ux, uy = dx / dist, dy / dist

        def t_for(half_w, half_h):
            tx = half_w / abs(ux) if ux != 0 else float("inf")
            ty = half_h / abs(uy) if uy != 0 else float("inf")
            return min(tx, ty)

        t0 = t_for(w0 / 2, h0 / 2)
        t1 = t_for(w1 / 2, h1 / 2)
        start = (x0 + t0 * ux, y0 + t0 * uy)
        end = (x1 - t1 * ux, y1 - t1 * uy)

        gap_bord_a_bord = math.hypot(end[0] - start[0], end[1] - start[1])
        if gap_bord_a_bord < ESPACEMENT_MIN_BOITES:
            raise ValueError(
                f"connect('{name_from}', '{name_to}') : espacement bord-a-bord "
                f"insuffisant ({gap_bord_a_bord:.4f}, minimum requis "
                f"{ESPACEMENT_MIN_BOITES:.4f}). En dessous de ce seuil, la "
                f"fleche est raccourcie moins que la marge minimale "
                f"(MARGE_MIN={MARGE_MIN}) et verify_no_overlap() la signalera "
                f"forcement comme un chevauchement avec l'une des deux "
                f"boites. Ecarte '{name_from}' et '{name_to}' (ou reduis leur "
                f"largeur/hauteur) avant de reessayer, plutot que de "
                f"decouvrir l'echec plus tard dans verify()/save()."
            )

        arrow = self.arrow(start, end, margin=margin, **arrow_kwargs)

        if label:
            mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
            if label_offset is None:
                # decale perpendiculairement a la fleche pour ne pas ecrire dessus
                perp = (-uy, ux)
                mx += perp[0] * 0.09
                my += perp[1] * 0.09
            else:
                mx += label_offset[0]
                my += label_offset[1]
            lk = {"fontsize": 8, "style": "italic", "color": BLEU}
            lk.update(label_kwargs or {})
            self.text((mx, my), label, **lk)
        return arrow

    def row(self, xs, y, box_w, box_h, textes, fontsize=9, prefix="r", **box_kwargs):
        """Rangee de boites reliees par des fleches horizontales, avec un
        espacement (xs[i+1]-xs[i] - box_w) deja compatible avec la marge
        anti-chevauchement par defaut. Retourne la liste des noms de boites."""
        half = box_w / 2
        noms = []
        for i, (x, txt) in enumerate(zip(xs, textes)):
            nom = f"{prefix}{i}"
            self.box(nom, (x, y), box_w, box_h, txt, fontsize=fontsize, **box_kwargs)
            noms.append(nom)
        for i in range(len(xs) - 1):
            self.arrow((xs[i] + half, y), (xs[i + 1] - half, y))
        return noms

    def save(self, path, max_tries=6, grow_factor=1.15, dpi=300):
        for _ in range(max_tries):
            ok, problemes = self.verify()
            if ok:
                self.fig.savefig(path, dpi=dpi, bbox_inches=None)
                import matplotlib.pyplot as plt
                plt.close(self.fig)
                return True, []
            w, h = self.fig.get_size_inches()
            self.fig.set_size_inches(w * grow_factor, h * grow_factor)
        import matplotlib.pyplot as plt
        plt.close(self.fig)
        return False, problemes
