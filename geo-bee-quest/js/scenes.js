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

    // --- labeled teaching diagrams ---

    landforms: wrap(`
      <path d="M0,0 H200 V16 Q170,22 150,18 Q120,28 96,22 Q60,32 40,24 Q18,30 0,24 Z" fill="${SAGE}"/>
      <path d="M38,24 Q46,17 53,24 L50,52 Q44,60 37,54 Z" fill="${SAGE}"/>
      <path d="M146,18 L156,16 L162,74 L152,78 Z" fill="${SAGE}"/>
      <path d="M136,110 Q138,86 156,76 Q184,68 200,72 V110 Z" fill="${SAND2}"/>
      <ellipse cx="76" cy="82" rx="14" ry="8" fill="${SAND2}"/>
      <text x="44" y="70" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">peninsula</text>
      <text x="76" y="101" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">island</text>
      <text x="138" y="49" font-size="8" text-anchor="end" fill="${INK}" font-weight="bold">isthmus</text>
      <line x1="140" y1="46" x2="150" y2="46" stroke="${INK}" stroke-width="1.4"/>`, "#a9cede"),

    riverparts: wrap(`
      <rect x="154" y="0" width="46" height="110" fill="#a9cede"/>
      <path d="M4,46 L30,10 L56,46 Z" fill="${INK}" opacity="0.85"/>
      <path d="M24,17 L30,10 L37,18 L32,23 L29,18 L26,23 Z" fill="${SNOW}"/>
      <path d="M140,70 L154,58 L154,88 Z" fill="${SAND2}" opacity="0.7"/>
      <path d="M32,42 C42,60 64,60 88,64 C112,68 124,68 140,71" stroke="#a9cede" stroke-width="5" fill="none" stroke-linecap="round"/>
      <path d="M108,8 C104,28 100,44 93,62" stroke="#a9cede" stroke-width="3" fill="none" stroke-linecap="round"/>
      <g stroke="#a9cede" stroke-width="3.5" fill="none" stroke-linecap="round">
        <path d="M140,71 L154,60"/><path d="M140,71 L155,73"/><path d="M140,71 L152,86"/></g>
      <text x="60" y="20" font-size="7" fill="${INK}" font-weight="bold">source</text>
      <line x1="58" y1="21" x2="45" y2="31" stroke="${INK}" stroke-width="1.2"/>
      <text x="112" y="30" font-size="7" fill="${INK}" font-weight="bold">tributary</text>
      <line x1="110" y1="29" x2="105" y2="31" stroke="${INK}" stroke-width="1.2"/>
      <text x="140" y="52" font-size="7" text-anchor="middle" fill="${INK}" font-weight="bold">delta</text>
      <text x="128" y="90" font-size="7" text-anchor="middle" fill="${INK}" font-weight="bold">mouth</text>
      <line x1="132" y1="82" x2="139" y2="74" stroke="${INK}" stroke-width="1.2"/>
      <text x="176" y="102" font-size="7" text-anchor="middle" fill="${INK}" font-weight="bold">sea</text>`, SAGE),

    latlon: wrap(`
      <circle cx="62" cy="56" r="42" fill="#a9cede" stroke="${INK}" stroke-width="2"/>
      <ellipse cx="46" cy="36" rx="11" ry="6" fill="${SAGE}"/>
      <ellipse cx="76" cy="72" rx="9" ry="5" fill="${SAGE}"/>
      <g stroke="${CREAM}" stroke-width="2" fill="none">
        <line x1="25" y1="38" x2="99" y2="38"/><line x1="25" y1="74" x2="99" y2="74"/>
        <line x1="62" y1="14" x2="62" y2="98"/><ellipse cx="62" cy="56" rx="20" ry="42"/></g>
      <line x1="20" y1="56" x2="104" y2="56" stroke="${TERRA2}" stroke-width="3"/>
      <text x="122" y="41" font-size="8" fill="${INK}" font-weight="bold">latitude</text>
      <line x1="100" y1="38" x2="118" y2="38" stroke="${INK}" stroke-width="1.4"/>
      <text x="122" y="59" font-size="8" fill="${TERRA2}" font-weight="bold">equator</text>
      <line x1="105" y1="56" x2="118" y2="56" stroke="${TERRA2}" stroke-width="2"/>
      <text x="122" y="88" font-size="8" fill="${INK}" font-weight="bold">longitude</text>
      <line x1="76" y1="89" x2="118" y2="85" stroke="${INK}" stroke-width="1.4"/>`, SKY2),

    hemispheres: wrap(`
      <circle cx="78" cy="56" r="40" fill="#a9cede" stroke="${INK}" stroke-width="2"/>
      <ellipse cx="58" cy="40" rx="11" ry="7" fill="${SAGE}"/>
      <ellipse cx="96" cy="70" rx="9" ry="6" fill="${SAGE}"/>
      <line x1="38" y1="56" x2="118" y2="56" stroke="${TERRA2}" stroke-width="4"/>
      <line x1="122" y1="56" x2="146" y2="56" stroke="${TERRA2}" stroke-width="2" stroke-dasharray="4 3"/>
      <text x="78" y="32" font-size="9" text-anchor="middle" fill="${INK}" font-weight="bold">North</text>
      <text x="76" y="88" font-size="9" text-anchor="middle" fill="${INK}" font-weight="bold">South</text>
      <text x="149" y="59" font-size="8" fill="${TERRA2}" font-weight="bold">equator</text>`, SKY2),

    volcanocut: wrap(`${cloud(36, 16, 0.8)}
      <rect y="82" width="200" height="8" fill="${SAGE}"/>
      <rect y="90" width="200" height="20" fill="${SAND2}"/>
      <path d="M36,90 L92,24 L108,24 L164,90 Z" fill="${INK}" opacity="0.88"/>
      <rect x="96" y="24" width="8" height="64" fill="${TERRA}"/>
      <ellipse cx="100" cy="96" rx="20" ry="12" fill="${TERRA}"/>
      <path d="M92,24 q8,6 16,0 l-2,8 q-6,4 -12,0 Z" fill="${TERRA}"/>
      <path d="M92,26 Q84,42 74,50" stroke="${TERRA}" stroke-width="3.5" fill="none" stroke-linecap="round"/>
      <path d="M96,12 q4,-8 8,0 q4,8 -4,8 q-8,0 -4,-8 Z" fill="${TERRA2}"/>
      <circle cx="84" cy="12" r="4" fill="${SAND2}" opacity="0.8"/><circle cx="116" cy="10" r="4.5" fill="${SAND2}" opacity="0.7"/>
      <text x="126" y="17" font-size="8" fill="${INK}" font-weight="bold">lava</text>
      <line x1="124" y1="15" x2="111" y2="15" stroke="${INK}" stroke-width="1.4"/>
      <text x="144" y="101" font-size="8" fill="${INK}" font-weight="bold">magma</text>
      <line x1="142" y1="98" x2="122" y2="96" stroke="${INK}" stroke-width="1.4"/>`),

    plates: wrap(`
      <rect y="94" width="200" height="16" fill="${TERRA2}"/>
      <path d="M0,70 H80 L90,94 H0 Z" fill="${SAND2}" stroke="${INK}" stroke-width="2"/>
      <path d="M200,70 H120 L110,94 H200 Z" fill="${TERRA}" opacity="0.85" stroke="${INK}" stroke-width="2"/>
      <path d="M64,72 L82,40 L96,54 L112,38 L134,72 Z" fill="${SAGE2}"/>
      <path d="M77,48 L82,40 L87,48 Z" fill="${SNOW}"/><path d="M107,46 L112,38 L117,46 Z" fill="${SNOW}"/>
      <line x1="12" y1="58" x2="50" y2="58" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M50,53 L60,58 L50,63 Z" fill="${INK}"/>
      <line x1="188" y1="58" x2="150" y2="58" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M150,53 L140,58 L150,63 Z" fill="${INK}"/>
      <text x="99" y="30" font-size="7" text-anchor="middle" fill="${INK}" font-weight="bold">mountains</text>
      <text x="38" y="85" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">plate</text>
      <text x="162" y="85" font-size="8" text-anchor="middle" fill="${CREAM}" font-weight="bold">plate</text>`, SKY2),

    biomestrip: wrap(`
      <rect width="66" height="110" fill="${SKY2}"/>
      <rect y="66" width="66" height="44" fill="${SNOW}"/>
      <g fill="${SNOW}"><circle cx="14" cy="26" r="1.8"/><circle cx="34" cy="16" r="1.5"/>
        <circle cx="50" cy="30" r="1.8"/><circle cx="22" cy="44" r="1.5"/><circle cx="54" cy="50" r="1.8"/></g>
      <ellipse cx="44" cy="74" rx="6" ry="2.6" fill="${SAND2}"/>
      <text x="33" y="101" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">tundra</text>
      <rect x="66" width="67" height="110" fill="${SKY}"/>
      <rect x="66" y="66" width="67" height="44" fill="${SAND2}"/>
      <circle cx="80" cy="16" r="6" fill="${SUN}" opacity="0.9"/>
      <line x1="101" y1="82" x2="101" y2="60" stroke="${INK}" stroke-width="3" stroke-linecap="round"/>
      <path d="M85,60 Q101,46 117,60 Q101,55 85,60 Z" fill="${SAGE2}"/>
      <path d="M101,68 L110,62" stroke="${INK}" stroke-width="2"/>
      <g stroke="${SAGE2}" stroke-width="2" stroke-linecap="round">
        <path d="M74,80 v-6"/><path d="M124,84 v-6"/><path d="M92,88 v-5"/></g>
      <text x="99" y="101" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">savanna</text>
      <rect x="133" width="67" height="110" fill="${SAGE}"/>
      <rect x="133" y="80" width="67" height="30" fill="${SAGE2}"/>
      <line x1="150" y1="80" x2="150" y2="62" stroke="${INK}" stroke-width="2"/>
      <line x1="170" y1="80" x2="170" y2="66" stroke="${INK}" stroke-width="2"/>
      <g fill="${SAGE2}"><circle cx="146" cy="44" r="13"/><circle cx="166" cy="36" r="15"/>
        <circle cx="188" cy="46" r="13"/><circle cx="154" cy="58" r="11"/><circle cx="178" cy="60" r="12"/></g>
      <g fill="${SAGE}" opacity="0.7"><circle cx="160" cy="48" r="7"/><circle cx="184" cy="52" r="6"/></g>
      <text x="166" y="101" font-size="8" text-anchor="middle" fill="${CREAM}" font-weight="bold">rainforest</text>
      <line x1="66" y1="0" x2="66" y2="110" stroke="${CREAM}" stroke-width="2.5"/>
      <line x1="133" y1="0" x2="133" y2="110" stroke="${CREAM}" stroke-width="2.5"/>`),

    compass8: wrap(`
      <circle cx="100" cy="56" r="34" fill="${CREAM}" stroke="${SAND2}" stroke-width="4"/>
      <circle cx="100" cy="56" r="26" fill="none" stroke="${SAND2}" stroke-width="1.5"/>
      <path d="M114,42 L105,56 L114,70 L100,61 L86,70 L95,56 L86,42 L100,51 Z" fill="${SAND2}"/>
      <path d="M100,26 L105,51 L130,56 L105,61 L100,86 L95,61 L70,56 L95,51 Z" fill="${TERRA}"/>
      <path d="M100,26 L105,51 L100,56 Z" fill="${TERRA2}"/>
      <circle cx="100" cy="56" r="4" fill="${INK}"/>
      <g fill="${INK}" font-weight="bold" text-anchor="middle">
        <text x="100" y="14" font-size="9">N</text><text x="100" y="104" font-size="9">S</text>
        <text x="144" y="59" font-size="9">E</text><text x="56" y="59" font-size="9">W</text>
        <text x="132" y="28" font-size="7">NE</text><text x="132" y="90" font-size="7">SE</text>
        <text x="68" y="90" font-size="7">SW</text><text x="68" y="28" font-size="7">NW</text></g>`, SKY2),

    greatlakes: wrap(`
      <path d="M24,32 Q34,14 68,18 Q102,22 98,34 Q86,44 56,40 Q30,42 24,32 Z" fill="#a9cede"/>
      <path d="M58,48 Q66,42 72,50 Q78,66 74,82 Q68,90 62,82 Q54,62 58,48 Z" fill="#a9cede"/>
      <path d="M88,44 Q104,36 112,48 Q118,62 104,68 Q90,64 86,52 Z" fill="#a9cede"/>
      <path d="M110,72 Q134,64 152,70 Q144,84 112,82 Z" fill="#a9cede"/>
      <path d="M146,54 Q168,48 180,54 Q172,66 148,64 Z" fill="#a9cede"/>
      <g fill="${INK}" font-weight="bold" text-anchor="middle">
        <text x="100" y="57" font-size="9">H</text>
        <text x="163" y="62" font-size="8">O</text>
        <text x="67" y="70" font-size="9">M</text>
        <text x="131" y="78" font-size="8">E</text>
        <text x="58" y="33" font-size="9">S</text></g>
      <text x="10" y="103" font-size="8" fill="${INK}" font-weight="bold">HOMES!</text>`, SAGE),

    strait: wrap(`
      <path d="M0,0 H200 V22 Q166,24 134,32 Q108,40 100,48 Q92,40 66,32 Q34,24 0,22 Z" fill="${SAGE}"/>
      <path d="M0,88 Q34,86 66,78 Q92,70 100,62 Q108,70 134,78 Q166,86 200,88 V110 H0 Z" fill="${SAND2}"/>
      ${waves(40)}
      <path d="M74,55 Q100,55 126,55" stroke="${CREAM}" stroke-width="2" fill="none" stroke-dasharray="4 3"/>
      <text x="100" y="14" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">strait</text>
      <line x1="100" y1="17" x2="100" y2="40" stroke="${INK}" stroke-width="1.5"/>
      <path d="M97,38 L100,45 L103,38 Z" fill="${INK}"/>
      <text x="22" y="62" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">sea</text>
      <text x="178" y="62" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">sea</text>`, "#a9cede"),

    monsoon: wrap(`
      <rect y="64" width="200" height="46" fill="#a9cede"/>
      <path d="M112,0 H196 Q192,34 172,60 Q160,78 150,90 Q138,72 126,48 Q116,24 112,0 Z" fill="${SAGE}"/>
      ${cloud(152, 16, 0.7)}
      <g stroke="#a9cede" stroke-width="2.2" stroke-linecap="round">
        <line x1="140" y1="28" x2="136" y2="38"/><line x1="152" y1="30" x2="148" y2="40"/>
        <line x1="164" y1="28" x2="160" y2="38"/><line x1="146" y1="44" x2="142" y2="54"/>
        <line x1="158" y1="46" x2="154" y2="56"/><line x1="170" y1="42" x2="166" y2="52"/></g>
      <g stroke="${INK}" stroke-width="3" fill="none" stroke-linecap="round">
        <path d="M12,88 Q52,84 88,62"/><path d="M8,66 Q48,60 82,40"/><path d="M24,102 Q66,98 104,78"/></g>
      <g fill="${INK}">
        <path d="M0,-4 L8,0 L0,4 Z" transform="translate(88,62) rotate(-31)"/>
        <path d="M0,-4 L8,0 L0,4 Z" transform="translate(82,40) rotate(-30)"/>
        <path d="M0,-4 L8,0 L0,4 Z" transform="translate(104,78) rotate(-28)"/></g>
      <text x="12" y="22" font-size="8" fill="${INK}" font-weight="bold">monsoon winds</text>
      <text x="150" y="66" font-size="7" text-anchor="middle" fill="${INK}" font-weight="bold">India</text>
      <text x="14" y="78" font-size="7" fill="${INK}" font-weight="bold">sea</text>`),

    poles: wrap(`
      <circle cx="100" cy="56" r="34" fill="#a9cede" stroke="${INK}" stroke-width="2"/>
      <ellipse cx="88" cy="54" rx="12" ry="7" fill="${SAGE}"/>
      <ellipse cx="112" cy="66" rx="8" ry="5" fill="${SAGE}"/>
      <path d="M78,30 A34,34 0 0 1 122,30 Q100,37 78,30 Z" fill="${SNOW}"/>
      <path d="M122,82 A34,34 0 0 1 78,82 Q100,75 122,82 Z" fill="${SNOW}"/>
      <text x="86" y="13" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">North Pole</text>
      <line x1="100" y1="16" x2="100" y2="26" stroke="${INK}" stroke-width="1.4"/>
      <g transform="translate(130,8)" stroke="${INK}" stroke-width="1" fill="${SNOW}">
        <rect x="-6" y="7" width="2.6" height="4.5" rx="1"/><rect x="3" y="7" width="2.6" height="4.5" rx="1"/>
        <ellipse cx="0" cy="5" rx="8" ry="4.5"/><circle cx="8" cy="1.5" r="3"/></g>
      <text x="86" y="106" font-size="8" text-anchor="middle" fill="${INK}" font-weight="bold">South Pole</text>
      <line x1="100" y1="92" x2="100" y2="97" stroke="${INK}" stroke-width="1.4"/>
      <g transform="translate(132,94)">
        <ellipse cx="0" cy="4" rx="5" ry="7.5" fill="${INK}"/>
        <ellipse cx="0" cy="5.5" rx="3" ry="5" fill="${SNOW}"/>
        <circle cx="0" cy="-4.5" r="3.4" fill="${INK}"/>
        <path d="M0,-4 l2.6,1.6 -2.6,1.6 Z" fill="${SUN}"/></g>`, SKY2),
  };
})();
