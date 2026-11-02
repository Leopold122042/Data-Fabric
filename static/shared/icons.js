// One outline vocabulary for navigation, metrics and record actions. No remote assets.
const paths = {
  database: ['M20 6c0 2.2-16 2.2-16 0s16-2.2 16 0Z','M4 6v12c0 2.7 16 2.7 16 0V6','M4 12c0 2.7 16 2.7 16 0'],
  layers: ['m12 3 9 5-9 5-9-5 9-5Z','m3 12 9 5 9-5','m3 16 9 5 9-5'],
  users: ['M16 21v-3a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v3','M9.5 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z','M17 4a3.5 3.5 0 0 1 0 7','M21 21v-3a4 4 0 0 0-3-3.9'],
  activity: ['M3 12h4l3-8 4 16 3-8h4'],
  file: ['M14 3H5v18h14V8l-5-5Z','M14 3v5h5','M8 12h8','M8 16h5'],
  shield: ['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z','m8 12 3 3 5-6'],
  alert: ['m12 3 10 18H2L12 3Z','M12 9v5','M12 17h.01'],
  check: ['m5 12 4 4L19 6'],
  clock: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z','M12 7v5l3 2'],
  arrow: ['M4 12h16','m14 6 6 6-6 6'],
  refresh: ['M20 7a9 9 0 1 0 1 8','M20 3v5h-5'],
  building: ['M4 21V3h12v18','M16 9h4v12H2','M8 7h4','M8 11h4','M8 15h4','M9 21v-3h2v3'],
  network: ['M9 3h6v6H9z','M3 15h6v6H3z','M15 15h6v6h-6z','M12 9v3','M6 15v-3h12v3'],
  clipboard: ['M8 5H5v16h14V5h-3','M9 3h6v4H9z','M8 12h8','M8 16h5'],
  message: ['M21 4H3v13h5v4l5-4h8V4Z','M7 9h10','M7 13h6'],
  package: ['m12 3 9 5v9l-9 5-9-5V8l9-5Z','m3 8 9 5 9-5','M12 13v9','m7.5 5.5 9 5'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z','M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'],
  chart: ['M4 3v18h17','M8 16v-5','M13 16V6','M18 16v-8'],
  lab: ['M9 3h6','M10 3v7L4 20h16l-6-10V3','M7 15h10'],
  image: ['M3 3h18v18H3z','m3 17 6-6 4 4 3-3 5 5','M16 7h.01'],
  grid: ['M3 3h7v7H3z','M14 3h7v7h-7z','M3 14h7v7H3z','M14 14h7v7h-7z'],
  key: ['M14 8a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z','m12 12 9 9','m17 17 3-3','m14 14 3-3'],
  info: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z','M12 11v6','M12 7h.01'],
  pulse: ['M3 5h18v14H3z','M6 12h3l2-4 3 8 2-4h2'],
  route: ['M6 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6Z','M18 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6Z','M6 9v6a3 3 0 0 0 3 3h6','M10 6h8v5'],
};
export function icon(name,className='') {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'ui-icon '+className}))svg.setAttribute(key,value);
  for(const d of paths[name]||paths.file){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}
  return svg;
}
