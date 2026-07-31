/* GeoBee Quest — Field Book illustrations.
 * Hand-authored flat SVG scenes in the Terra Explorer palette, one per
 * chapter. No external images, no AI — just shapes.
 */
window.GEO_SCENES = (function () {
  // palette
  const SKY = "#cfe4ee", SKY2 = "#e3f0f6", SAND = "#e9e2d3", SAND2 = "#dccfae",
    SAGE = "#8fae4f", SAGE2 = "#56642b", TERRA = "#e2725b", TERRA2 = "#9f402d",
    CREAM = "#fffdf7", INK = "#56423e", SUN = "#f5c518", SNOW = "#ffffff";

  const wrap = (inner, bg) =>
    `<svg class="scene" viewBox="0 0 200 110" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <rect width="200" height="110" rx="10" fill="${bg || SKY}"/>${inner}</svg>`;

  const sun = (x, y, r) => `<circle cx="${x}" cy="${y}" r="${r || 10}" fill="${SUN}" opacity="0.9"/>`;
  const cloud = (x, y, s) => `<g transform="translate(${x},${y}) scale(${s || 1})" fill="${CREAM}" opacity="0.9">
    <ellipse cx="0" cy="0" rx="14" ry="6"/><ellipse cx="10" cy="-3" rx="10" ry="5"/><ellipse cx="-10" cy="-2" rx="9" ry="5"/></g>`;
  const ground = (color, d) => `<path d="${d || "M0,78 Q50,68 100,76 T200,74 V110 H0 Z"}" fill="${color || SAGE}"/>`;
  const waves = (y) => `<g stroke="${CREAM}" stroke-width="2.5" fill="none" opacity="0.7" stroke-linecap="round">
    <path d="M14,${y} q7,-5 14,0 t14,0"/><path d="M120,${y + 8} q7,-5 14,0 t14,0"/><path d="M60,${y + 16} q7,-5 14,0 t14,0"/></g>`;

  return {
    compass: wrap(`${sun(170, 20)}${cloud(45, 22, 0.8)}
      <circle cx="100" cy="60" r="34" fill="${CREAM}" stroke="${SAND2}" stroke-width="4"/>
      <circle cx="100" cy="60" r="26" fill="none" stroke="${SAND2}" stroke-width="1.5"/>
      <path d="M100,38 L106,60 L100,82 L94,60 Z" fill="${TERRA}"/>
      <path d="M100,38 L106,60 H94 Z" fill="${TERRA2}"/>
      <circle cx="100" cy="60" r="4" fill="${INK}"/>
      <text x="100" y="34" font-size="9" text-anchor="middle" fill="${INK}" font-weight="bold">N</text>`, SKY2),

    ocean: wrap(`${sun(28, 22, 11)}${cloud(150, 20, 0.9)}
      <rect y="52" width="200" height="58" fill="#a9cede"/>${waves(66)}
      <path d="M52,52 q14,-16 34,-8 q16,6 8,8 Z" fill="${SAGE}"/>
      <path d="M128,52 q10,-11 24,-5 q10,4 4,5 Z" fill="${SAND2}"/>`),

    bridge: wrap(`${sun(100, 18, 9)}
      <path d="M0,72 Q30,52 62,66 L62,110 H0 Z" fill="${SAGE}"/>
      <path d="M200,70 Q170,50 138,64 L138,110 H200 Z" fill="${SAND2}"/>
      <rect y="76" width="200" height="34" fill="#a9cede"/>${waves(88)}
      <path d="M56,66 C86,44 114,44 144,64" stroke="${TERRA2}" stroke-width="4" fill="none" stroke-linecap="round"/>
      <line x1="76" y1="55" x2="76" y2="70" stroke="${TERRA2}" stroke-width="3"/>
      <line x1="100" y1="50" x2="100" y2="72" stroke="${TERRA2}" stroke-width="3"/>
      <line x1="124" y1="55" x2="124" y2="70" stroke="${TERRA2}" stroke-width="3"/>`),

    capitol: wrap(`${sun(30, 20)}${cloud(160, 24, 0.8)}${ground(SAGE)}
      <rect x="62" y="58" width="76" height="26" rx="3" fill="${CREAM}"/>
      <rect x="70" y="63" width="6" height="21" fill="${SAND2}"/><rect x="84" y="63" width="6" height="21" fill="${SAND2}"/>
      <rect x="110" y="63" width="6" height="21" fill="${SAND2}"/><rect x="124" y="63" width="6" height="21" fill="${SAND2}"/>
      <path d="M84,58 a16,14 0 0 1 32,0 Z" fill="${CREAM}" stroke="${SAND2}" stroke-width="2"/>
      <rect x="97" y="30" width="6" height="12" fill="${CREAM}"/>
      <circle cx="100" cy="28" r="3.4" fill="${TERRA}"/>`),

    mountains: wrap(`${sun(168, 22)}${cloud(48, 18, 0.9)}
      <path d="M0,92 L52,34 L96,92 Z" fill="${INK}" opacity="0.85"/>
      <path d="M42,46 L52,34 L63,46 L57,52 L52,46 L47,53 Z" fill="${SNOW}"/>
      <path d="M60,92 L118,44 L176,92 Z" fill="${TERRA2}" opacity="0.8"/>
      <path d="M108,52 L118,44 L129,53 L122,58 L117,52 L112,59 Z" fill="${SNOW}"/>
      ${ground(SAGE, "M0,86 Q60,78 120,86 T200,84 V110 H0 Z")}
      <path d="M86,110 Q95,92 108,86 Q120,92 124,110 Z" fill="#a9cede"/>`),

    river: wrap(`${sun(30, 20)}${cloud(158, 22, 0.9)}
      ${ground(SAGE, "M0,60 Q60,50 120,60 T200,58 V110 H0 Z")}
      <path d="M84,58 C70,72 128,80 104,110 L142,110 C160,84 96,76 122,58 Z" fill="#a9cede"/>
      <ellipse cx="46" cy="80" rx="16" ry="6" fill="${SAGE2}" opacity="0.5"/>
      <ellipse cx="164" cy="92" rx="18" ry="7" fill="${SAGE2}" opacity="0.5"/>`),

    city: wrap(`${sun(24, 18, 9)}
      <rect x="34" y="46" width="20" height="46" rx="2" fill="${CREAM}"/>
      <rect x="60" y="30" width="24" height="62" rx="2" fill="${SAND2}"/>
      <rect x="90" y="52" width="18" height="40" rx="2" fill="${CREAM}"/>
      <rect x="114" y="38" width="26" height="54" rx="2" fill="${TERRA}" opacity="0.75"/>
      <rect x="146" y="56" width="20" height="36" rx="2" fill="${SAND2}"/>
      <g fill="${SUN}" opacity="0.85">
        <rect x="64" y="36" width="5" height="5"/><rect x="74" y="36" width="5" height="5"/>
        <rect x="64" y="47" width="5" height="5"/><rect x="119" y="44" width="5" height="5"/>
        <rect x="129" y="44" width="5" height="5"/><rect x="119" y="55" width="5" height="5"/>
        <rect x="38" y="52" width="4" height="4"/><rect x="46" y="52" width="4" height="4"/></g>
      ${ground(SAGE, "M0,90 Q100,84 200,90 V110 H0 Z")}`),

    flags: wrap(`${sun(172, 20)}${cloud(30, 20, 0.8)}${ground(SAGE)}
      <line x1="52" y1="30" x2="52" y2="82" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M55,32 h34 l-8,9 8,9 h-34 Z" fill="${TERRA}"/>
      <line x1="104" y1="22" x2="104" y2="82" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M107,24 h38 l-9,10 9,10 h-38 Z" fill="${SAGE2}"/>
      <line x1="156" y1="34" x2="156" y2="82" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M159,36 h30 l-7,8 7,8 h-30 Z" fill="${SUN}"/>`),

    hills: wrap(`${sun(166, 20)}${cloud(40, 22, 0.9)}
      <path d="M0,74 Q50,44 100,72 T200,70 V110 H0 Z" fill="${SAGE}"/>
      <path d="M0,88 Q60,66 120,86 T200,86 V110 H0 Z" fill="${SAGE2}" opacity="0.65"/>
      <g fill="${TERRA}"><circle cx="58" cy="62" r="3"/><circle cx="72" cy="66" r="3"/><circle cx="64" cy="72" r="3"/></g>
      <path d="M130,60 l4,-12 4,12 Z" fill="${SAGE2}"/><rect x="132.6" y="60" width="2.8" height="6" fill="${INK}"/>`),

    wonders: wrap(`${sun(30, 22, 11)}
      <path d="M28,88 L66,40 L104,88 Z" fill="${SAND2}"/>
      <path d="M56,88 L66,40 L76,88 Z" fill="#cbb98f"/>
      <line x1="142" y1="88" x2="142" y2="30" stroke="${INK}" stroke-width="4" stroke-linecap="round"/>
      <path d="M142,30 L128,88 M142,30 L156,88" stroke="${INK}" stroke-width="2.5"/>
      <line x1="133" y1="58" x2="151" y2="58" stroke="${INK}" stroke-width="2"/>
      <line x1="129" y1="74" x2="155" y2="74" stroke="${INK}" stroke-width="2"/>
      ${ground(SAND, "M0,88 Q100,82 200,88 V110 H0 Z")}`),

    savanna: wrap(`${sun(160, 24, 13)}
      ${ground("#d9c98e", "M0,72 Q60,64 120,72 T200,70 V110 H0 Z")}
      <line x1="52" y1="88" x2="52" y2="52" stroke="${INK}" stroke-width="3.6" stroke-linecap="round"/>
      <path d="M22,52 Q52,30 84,52 Q52,44 22,52 Z" fill="${SAGE2}"/>
      <path d="M52,64 L64,56" stroke="${INK}" stroke-width="2.4"/>
      <g fill="${INK}"><ellipse cx="128" cy="82" rx="10" ry="6"/><rect x="135" y="70" width="3" height="12" rx="1.5"/>
      <circle cx="137" cy="68" r="3.4"/><rect x="121" y="86" width="2.6" height="7"/><rect x="132" y="86" width="2.6" height="7"/></g>`),

    festival: wrap(`
      <path d="M0,16 Q50,34 100,16 T200,16" stroke="${INK}" stroke-width="2" fill="none"/>
      <g>
        <path d="M22,19 l6,12 l6,-11 Z" fill="${TERRA}"/><path d="M52,24 l6,12 l6,-11 Z" fill="${SUN}"/>
        <path d="M82,20 l6,12 l6,-11 Z" fill="${SAGE}"/><path d="M112,23 l6,12 l6,-11 Z" fill="${TERRA}"/>
        <path d="M142,19 l6,12 l6,-11 Z" fill="${SUN}"/><path d="M170,23 l6,12 l6,-11 Z" fill="${SAGE}"/></g>
      <circle cx="100" cy="78" r="24" fill="${CREAM}" stroke="${SAND2}" stroke-width="3"/>
      <path d="M84,74 q16,-12 32,0 q-16,8 -32,0 Z" fill="${TERRA}"/>
      <circle cx="94" cy="70" r="2.6" fill="${SUN}"/><circle cx="106" cy="69" r="2.6" fill="${SAGE}"/>
      <line x1="132" y1="62" x2="140" y2="88" stroke="${INK}" stroke-width="2.6" stroke-linecap="round"/>
      <line x1="68" y1="62" x2="60" y2="88" stroke="${INK}" stroke-width="2.6" stroke-linecap="round"/>`, SKY2),

    taj: wrap(`${sun(26, 20)}${cloud(168, 22, 0.8)}
      <rect x="52" y="62" width="96" height="26" rx="3" fill="${CREAM}"/>
      <path d="M82,62 v-8 a18,16 0 0 1 36,0 v8 Z" fill="${CREAM}" stroke="${SAND2}" stroke-width="2"/>
      <path d="M100,34 q8,6 0,12 q-8,-6 0,-12 Z" fill="${CREAM}" stroke="${SAND2}" stroke-width="1.6"/>
      <rect x="96" y="66" width="8" height="22" rx="4" fill="${SAND2}"/>
      <line x1="58" y1="62" x2="58" y2="40" stroke="${CREAM}" stroke-width="5" stroke-linecap="round"/>
      <line x1="142" y1="62" x2="142" y2="40" stroke="${CREAM}" stroke-width="5" stroke-linecap="round"/>
      <rect y="92" width="200" height="18" fill="#a9cede"/>`),

    volcano: wrap(`${cloud(40, 20, 0.9)}
      <path d="M58,92 L88,34 L112,34 L142,92 Z" fill="${INK}" opacity="0.85"/>
      <path d="M88,34 q12,8 24,0 l-4,14 q-8,6 -16,0 Z" fill="${TERRA}"/>
      <path d="M96,20 q4,-8 8,0 q4,8 -4,8 q-8,0 -4,-8 Z" fill="${TERRA2}"/>
      <circle cx="86" cy="16" r="4" fill="${SAND2}" opacity="0.8"/><circle cx="116" cy="14" r="5" fill="${SAND2}" opacity="0.7"/>
      ${ground(SAGE, "M0,90 Q100,84 200,90 V110 H0 Z")}`),

    harvest: wrap(`${sun(170, 20)}${ground("#d9c98e", "M0,70 Q100,62 200,70 V110 H0 Z")}
      <g stroke="${SAGE2}" stroke-width="2.4" stroke-linecap="round">
        <path d="M30,88 V64 M30,70 l-7,-7 M30,70 l7,-7 M30,78 l-7,-7 M30,78 l7,-7"/>
        <path d="M52,88 V62 M52,68 l-7,-7 M52,68 l7,-7 M52,76 l-7,-7 M52,76 l7,-7"/></g>
      <rect x="92" y="66" width="30" height="22" rx="3" fill="${SAND2}" stroke="${INK}" stroke-width="2"/>
      <rect x="126" y="72" width="24" height="16" rx="3" fill="${TERRA}" opacity="0.8" stroke="${INK}" stroke-width="2"/>
      <line x1="92" y1="77" x2="122" y2="77" stroke="${INK}" stroke-width="1.6"/>
      <circle cx="170" cy="66" r="9" fill="${TERRA}"/><path d="M170,58 q3,-5 7,-5" stroke="${SAGE2}" stroke-width="2.4" fill="none"/>`),

    ship: wrap(`${sun(30, 20)}${cloud(160, 18, 0.8)}
      <rect y="76" width="200" height="34" fill="#a9cede"/>${waves(92)}
      <path d="M68,78 L134,78 L122,92 L80,92 Z" fill="${TERRA2}"/>
      <line x1="100" y1="78" x2="100" y2="34" stroke="${INK}" stroke-width="3"/>
      <path d="M100,36 Q124,50 100,66 Z" fill="${CREAM}" stroke="${SAND2}" stroke-width="1.6"/>
      <path d="M100,40 Q82,52 100,64 Z" fill="${SAND}" stroke="${SAND2}" stroke-width="1.6"/>
      <path d="M100,34 l12,4 -12,4 Z" fill="${TERRA}"/>`),

    peaks: wrap(`${sun(24, 18, 9)}${cloud(150, 16, 0.9)}
      <path d="M0,92 L44,40 L82,92 Z" fill="${TERRA2}" opacity="0.75"/>
      <path d="M52,92 L106,26 L160,92 Z" fill="${INK}" opacity="0.85"/>
      <path d="M96,38 L106,26 L117,39 L110,45 L105,38 L100,46 Z" fill="${SNOW}"/>
      <path d="M132,92 L172,50 L200,92 Z" fill="${TERRA2}" opacity="0.6"/>
      ${ground(SAGE, "M0,90 Q100,84 200,90 V110 H0 Z")}`),

    canyon: wrap(`${sun(166, 18, 10)}
      <rect y="34" width="200" height="76" fill="${SAND}"/>
      <path d="M0,34 L36,34 L52,110 L0,110 Z" fill="${TERRA2}" opacity="0.85"/>
      <path d="M200,34 L152,34 L138,110 L200,110 Z" fill="${TERRA2}" opacity="0.85"/>
      <path d="M36,34 L52,110 M152,34 L138,110" stroke="${INK}" stroke-width="2" opacity="0.3"/>
      <g stroke="${TERRA}" stroke-width="4" opacity="0.6">
        <line x1="8" y1="56" x2="42" y2="56"/><line x1="158" y1="56" x2="194" y2="56"/>
        <line x1="12" y1="76" x2="46" y2="76"/><line x1="154" y1="76" x2="190" y2="76"/></g>
      <path d="M84,110 Q95,80 90,50 Q88,40 94,34 L102,34 Q96,44 98,54 Q104,84 96,110 Z" fill="#a9cede"/>`, SKY2),

    ice: wrap(`${cloud(40, 18, 0.9)}${cloud(150, 26, 0.7)}
      <rect y="64" width="200" height="46" fill="#a9cede"/>${waves(78)}
      <path d="M28,64 L44,40 L66,64 Z" fill="${SNOW}"/>
      <path d="M44,64 L58,52 L74,64 Z" fill="${SKY2}"/>
      <path d="M120,64 L148,30 L182,64 Z" fill="${SNOW}"/>
      <path d="M148,64 L166,46 L188,64 Z" fill="${SKY2}"/>
      <g transform="translate(96,52)">
        <ellipse cx="0" cy="6" rx="6" ry="9" fill="${INK}"/>
        <ellipse cx="0" cy="8" rx="3.6" ry="6" fill="${SNOW}"/>
        <circle cx="0" cy="-4" r="4" fill="${INK}"/>
        <path d="M0,-3 l3,2 -3,2 Z" fill="${SUN}"/></g>`),
  };
})();
