import { cleanDisplayText } from '../lib/display-text';

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error(msg);
}

const rueda = `RUEDA IN THE PAHK IS BACK! 🎉☀️💃🏽🕺🏽

Join us Sundays for this season of outdoor dancing, community, chaos, laughter, and nonstop rueda magic in Central Square!

🕕 6 PM — FREE Rueda de Casino lesson for all levels
No partner? No experience? No problem! Just show up ready to learn and have fun 😎

🕖 7 PM — Social dancing in the sunshine ‘til sundown

📍 Jill Brown Rhone Park — Central Square, Cambridge

Let’s make the park spin again! ✨

Source: https://www.instagram.com/p/DY5afzhhsK7/`;

const cleaned = cleanDisplayText(rueda);
assert(!cleaned.includes('RUEDA IN THE PAHK IS BACK'), 'shout opener dropped');
assert(!cleaned.includes('Source:'), 'Source line dropped');
assert(!cleaned.includes('instagram.com'), 'source URL dropped');
assert(!/[\u{1F300}-\u{1FAFF}]/u.test(cleaned), 'pictographs stripped');
assert(cleaned.includes('Join us Sundays'), 'body paragraph kept');
assert(cleaned.includes('6 PM'), 'schedule line kept');

const onlyShout = cleanDisplayText('SALSA NIGHT IS BACK!!! 🎉🎉🎉');
assert(onlyShout.toLowerCase().includes('salsa night'), 'keep shout line when it is the only content');

assert(cleanDisplayText(cleaned) === cleaned, 'idempotent');

console.log('test_display_text: ok');
