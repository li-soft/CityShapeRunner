import { lazy, Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  analyseInkproof,
  buildLessonPack,
  editRoute,
  generate as generateRoute,
  fetchDestinations,
  fetchOccasions,
  fetchShapePlacementPreview,
  fetchShapeTemplates,
  interpretRoute,
  listGallery,
  publishGalleryImage,
  recordRouteAcceptance,
  repairRecognition,
  requestAccessibilityReadiness,
  requestNightReadiness,
  requestRouteLandmarks,
  requestTimedReadiness,
  removeGalleryImage,
  rescueArtwork,
} from "./api.js";
import RouteSetupPanel from "./RouteSetupPanel.jsx";
import SvgShapeUpload from "./SvgShapeUpload.jsx";
import GroupWorkspace from "./GroupWorkspace.jsx";
import WaitingSketchGame from "./WaitingSketchGame.jsx";
import WaitingRunnerGame from "./WaitingRunnerGame.jsx";
import { shuffled } from "./inspiration.js";
import { distanceMetres } from "./routeWalkthrough.js";
import {
  EMPTY_ROUTE_PREFERENCES,
  buildRouteSetupPayload,
} from "./routeSetup.js";

const RouteMap = lazy(() => import("./RouteMap.jsx"));
const RouteWalkthrough = lazy(() => import("./RouteWalkthrough.jsx"));
const ShapePlacementMap = lazy(() => import("./ShapePlacementMap.jsx"));
const GALLERY_REMOVAL_STORAGE_KEY = "gps-art-gallery-removal-tokens-v1";
const FEATURED_GALLERY_STORAGE_KEY = "gps-art-featured-gallery-id-v1";
const EXAMPLE_PROMPT_STORAGE_KEY = "gps-art-example-prompt-v1";

const CORE_IDEAS = [
  { glyph: "♥", label: "Heart", category: "Simple shapes", featured: true, prompt: "a heart run in Budapest, about 8 km" },
  { glyph: "★", label: "Star", category: "Simple shapes", featured: true, prompt: "a star bike route in Debrecen, about 20 km" },
  { glyph: "○", label: "Circle", category: "Simple shapes", featured: true, prompt: "a circle run in Kecskemét, about 8 km" },
  { glyph: "◆", label: "Diamond", category: "Simple shapes", featured: true, prompt: "a diamond run in Szombathely, about 8 km" },
  { glyph: "△", label: "Triangle", category: "Simple shapes", featured: true, prompt: "a triangle run in Tatabánya, about 10 km" },
  { glyph: "□", label: "Square", category: "Simple shapes", featured: true, prompt: "a square run in Szombathely, about 8 km" },
  { glyph: "∞", label: "Infinity", category: "Simple shapes", featured: true, prompt: "an infinity run in Szeged, about 10 km" },
  { glyph: "➜", label: "Arrow", category: "Simple shapes", featured: true, prompt: "an arrow run in Siófok, about 8 km" },
  { glyph: "✚", label: "Cross", category: "Simple shapes", featured: true, prompt: "a cross run in Nyíregyháza, about 8 km" },
  { glyph: "ϟ", label: "Lightning", category: "Simple shapes", featured: true, prompt: "a lightning run in Cegléd, about 8 km" },
  { glyph: "∿", label: "Wave", category: "Simple shapes", featured: true, prompt: "a wave run in Siófok, about 8 km" },
  { glyph: "☾", label: "Moon", category: "Simple shapes", featured: true, prompt: "a moon run in Kecskemét, about 8 km" },
  { glyph: "⬡", label: "Hexagon", category: "Simple shapes", prompt: "a hexagon run in Győr, about 8 km" },
  { glyph: "8", label: "Octagon", category: "Simple shapes", prompt: "an octagon run in Kecskemét, about 10 km" },
  { glyph: "◒", label: "Teardrop", category: "Simple shapes", prompt: "a teardrop run in Szeged, about 10 km" },
  { glyph: "◈", label: "Shield", category: "Simple shapes", prompt: "a shield run in Debrecen, about 12 km" },
  { glyph: "♧", label: "Clover", category: "Simple shapes", prompt: "a clover run in Szombathely, about 12 km" },
  { glyph: "◎", label: "Spiral", category: "Simple shapes", prompt: "a spiral bike route in Budapest, about 20 km" },
  { glyph: "⧖", label: "Hourglass", category: "Simple shapes", prompt: "an hourglass run in Nyíregyháza, about 12 km" },
  { glyph: "✿", label: "Flower", category: "Nature", prompt: "a flower run in Debrecen, about 12 km" },
  { glyph: "♣", label: "Tree", category: "Nature", prompt: "a tree run in Tatabánya, about 10 km" },
  { glyph: "⌃", label: "Mountain", category: "Nature", prompt: "a mountain run in Miskolc, about 10 km" },
  { glyph: "Ƹ", label: "Butterfly", category: "Nature", prompt: "a butterfly run in Kecskemét, about 10 km" },
  { glyph: "☀", label: "Sun", category: "Nature", prompt: "a sun bike route in Debrecen, about 20 km" },
  { glyph: "◜", label: "Leaf", category: "Nature", prompt: "a leaf run in Eger, about 10 km" },
  { glyph: "♠", label: "Pine tree", category: "Nature", prompt: "a pine tree run in Sopron, about 12 km" },
  { glyph: "◠", label: "Mushroom", category: "Nature", prompt: "a mushroom run in Tatabánya, about 12 km" },
  { glyph: "☁", label: "Cloud", category: "Nature", prompt: "a cloud run in Győr, about 10 km" },
  { glyph: "❄", label: "Snowflake", category: "Nature", prompt: "a snowflake bike route in Debrecen, about 24 km" },
  { glyph: "Ψ", label: "Cactus", category: "Nature", prompt: "a cactus run in Szeged, about 12 km" },
  { glyph: "●", label: "Apple", category: "Nature", prompt: "an apple run in Kecskemét, about 10 km" },
  { glyph: "◐", label: "Pear", category: "Nature", prompt: "a pear run in Eger, about 10 km" },
  { glyph: "♤", label: "Tulip", category: "Nature", prompt: "a tulip run in Debrecen, about 12 km" },
  { glyph: "♨", label: "Flame", category: "Nature", prompt: "a flame run in Cegléd, about 12 km" },
  { glyph: "✣", label: "Maple leaf", category: "Nature", prompt: "a maple leaf bike route in Budapest, about 24 km" },
  { glyph: "⌃", label: "Cat", category: "Animals", prompt: "a cat run in Tatabánya, about 10 km" },
  { glyph: "⌁", label: "Dog", category: "Animals", prompt: "a dog run in Tatabánya, about 10 km" },
  { glyph: "≈", label: "Fish", category: "Animals", prompt: "a fish run in Siófok, about 12 km" },
  { glyph: "⌁", label: "Bird", category: "Animals", prompt: "a bird run in Szeged, about 12 km" },
  { glyph: "◠", label: "Rabbit", category: "Animals", prompt: "a rabbit bike route in Kecskemét, about 20 km" },
  { glyph: "♞", label: "Horse", category: "Animals", prompt: "a horse bike route in Debrecen, about 22 km" },
  { glyph: "∽", label: "Dolphin", category: "Animals", prompt: "a dolphin bike route in Siófok, about 20 km" },
  { glyph: "ϟ", label: "Dragon", category: "Animals", prompt: "a dragon bike route in Budapest, about 28 km" },
  { glyph: "◉", label: "Turtle", category: "Animals", prompt: "a turtle run in Keszthely, about 14 km" },
  { glyph: "≋", label: "Whale", category: "Animals", prompt: "a whale bike route in Siófok, about 22 km" },
  { glyph: "▶", label: "Shark", category: "Animals", prompt: "a shark bike route in Szeged, about 22 km" },
  { glyph: "⋏", label: "Fox", category: "Animals", prompt: "a fox run in Sopron, about 14 km" },
  { glyph: "◉", label: "Owl", category: "Animals", prompt: "an owl run in Eger, about 14 km" },
  { glyph: "≈", label: "Duck", category: "Animals", prompt: "a duck run in Keszthely, about 12 km" },
  { glyph: "@", label: "Snail", category: "Animals", prompt: "a snail run in Győr, about 12 km" },
  { glyph: "Ɛ", label: "Elephant", category: "Animals", prompt: "an elephant bike route in Budapest, about 26 km" },
  { glyph: "⌁", label: "Bat", category: "Animals", prompt: "a bat run in Miskolc, about 14 km" },
  { glyph: "◉", label: "Bear", category: "Animals", prompt: "a bear run in Tatabánya, about 14 km" },
  { glyph: "◒", label: "Penguin", category: "Animals", prompt: "a penguin run in Szombathely, about 14 km" },
  { glyph: "🐞", label: "Bug", category: "Animals", prompt: "a bug run in Tatabánya, about 8 km" },
  { glyph: "⚓", label: "Anchor", category: "Objects", prompt: "an anchor bike route in Siófok, about 22 km" },
  { glyph: "⚿", label: "Key", category: "Objects", prompt: "a key bike route in Győr, about 18 km" },
  { glyph: "▣", label: "Mug", category: "Objects", prompt: "a mug bike route in Budapest, about 20 km" },
  { glyph: "♪", label: "Musical note", category: "Objects", prompt: "a musical note bike route in Szeged, about 18 km" },
  { glyph: "⛵", label: "Sailboat", category: "Objects", prompt: "a sailboat bike route in Siófok, about 24 km" },
  { glyph: "⌂", label: "House", category: "Objects", prompt: "a house run in Kecskemét, about 12 km" },
  { glyph: "↑", label: "Rocket", category: "Objects", prompt: "a rocket bike route in Debrecen, about 22 km" },
  { glyph: "✈", label: "Airplane", category: "Objects", prompt: "an airplane bike route in Budapest, about 24 km" },
  { glyph: "▰", label: "Car", category: "Objects", prompt: "a car run in Győr, about 14 km" },
  { glyph: "☂", label: "Umbrella", category: "Objects", prompt: "an umbrella run in Szeged, about 14 km" },
  { glyph: "◇", label: "Bell", category: "Objects", prompt: "a bell run in Eger, about 12 km" },
  { glyph: "♫", label: "Guitar", category: "Objects", prompt: "a guitar bike route in Budapest, about 24 km" },
  { glyph: "♜", label: "Castle", category: "Objects", prompt: "a castle bike route in Székesfehérvár, about 24 km" },
  { glyph: "♕", label: "Trophy", category: "Objects", prompt: "a trophy bike route in Debrecen, about 22 km" },
  { glyph: "♛", label: "Crown", category: "Symbols", prompt: "a crown run in Székesfehérvár, about 10 km" },
  { glyph: "☠", label: "Skull", category: "Symbols", prompt: "a skull bike route in Miskolc, about 22 km" },
  { glyph: "≋", label: "DNA helix", category: "Symbols", prompt: "a DNA helix bike route in Budapest, about 25 km" },
  { glyph: "◰", label: "Speech bubble", category: "Symbols", prompt: "a speech bubble run in Debrecen, about 12 km" },
  { glyph: "⌖", label: "Location pin", category: "Symbols", prompt: "a location pin run in Győr, about 12 km" },
  { glyph: "A", label: "Letter A", category: "Letters, numbers & text", prompt: "draw the letter A while running in Miskolc, about 10 km" },
  { glyph: "C", label: "Letter C", category: "Letters, numbers & text", prompt: "draw the letter C while running in Szeged, about 8 km" },
  { glyph: "L", label: "Letter L", category: "Letters, numbers & text", prompt: "draw the letter L while running in Kecskemét, about 8 km" },
  { glyph: "M", label: "Letter M", category: "Letters, numbers & text", prompt: "draw the letter M while running in Debrecen, about 10 km" },
  { glyph: "N", label: "Letter N", category: "Letters, numbers & text", prompt: "draw the letter N while running in Nyíregyháza, about 10 km" },
  { glyph: "S", label: "Letter S", category: "Letters, numbers & text", prompt: "draw the letter S while running in Szeged, about 10 km" },
  { glyph: "U", label: "Letter U", category: "Letters, numbers & text", prompt: "draw the letter U while running in Győr, about 10 km" },
  { glyph: "V", label: "Letter V", category: "Letters, numbers & text", prompt: "draw the letter V while running in Veszprém, about 8 km" },
  { glyph: "Z", label: "Letter Z", category: "Letters, numbers & text", prompt: "draw the letter Z while running in Zalaegerszeg, about 10 km" },
  { glyph: "2", label: "Number 2", category: "Letters, numbers & text", prompt: "draw the number 2 while running in Eger, about 8 km" },
  { glyph: "7", label: "Number 7", category: "Letters, numbers & text", prompt: "draw the number 7 while running in Debrecen, about 8 km" },
  { glyph: "42", label: "Number 42", category: "Letters, numbers & text", prompt: "draw the number 42 while cycling in Eger, about 20 km" },
  { glyph: "GPS", label: "Text GPS", category: "Letters, numbers & text", prompt: "write GPS while cycling in Budapest, about 25 km" },
];

// Authored as routable, single-outline silhouettes in the backend catalogue.
// The varied example cities also make the expanded European coverage easy to
// discover without turning the main six quick choices into a wall of buttons.
const ADDITIONAL_SHAPE_IDEAS = [
  { glyph: "🌰", label: "Acorn", category: "Nature", prompt: "an acorn run in Uppsala, about 10 km" },
  { glyph: "🐜", label: "Ant", category: "Animals", prompt: "an ant run in Poznań, about 12 km" },
  { glyph: "🪓", label: "Axe", category: "Objects", prompt: "an axe bike route in Glasgow, about 20 km" },
  { glyph: "🎈", label: "Balloon", category: "Objects", prompt: "a balloon run in Lyon, about 10 km" },
  { glyph: "🍌", label: "Banana", category: "Nature", prompt: "a banana run in Valencia, about 10 km" },
  { glyph: "🍾", label: "Bottle", category: "Objects", prompt: "a bottle run in Brno, about 12 km" },
  { glyph: "🎀", label: "Bow tie", category: "Objects", prompt: "a bow tie run in Turin, about 10 km" },
  { glyph: "🥦", label: "Broccoli", category: "Nature", prompt: "broccoli GPS art in Leipzig, running about 12 km" },
  { glyph: "🚌", label: "Bus", category: "Objects", prompt: "a bus bike route in Birmingham, about 20 km" },
  { glyph: "📷", label: "Camera", category: "Objects", prompt: "a camera bike route in Manchester, about 22 km" },
  { glyph: "🕯️", label: "Candle", category: "Objects", prompt: "a candle run in Graz, about 12 km" },
  { glyph: "🍬", label: "Candy", category: "Objects", prompt: "a wrapped candy run in Malmö, about 12 km" },
  { glyph: "♟️", label: "Chess pawn", category: "Symbols", prompt: "a chess pawn run in Prague, about 12 km" },
  { glyph: "🧭", label: "Compass", category: "Symbols", prompt: "a compass bike route in Rotterdam, about 22 km" },
  { glyph: "🦀", label: "Crab", category: "Animals", prompt: "a crab run in A Coruña, about 12 km" },
  { glyph: "🦕", label: "Dinosaur", category: "Animals", prompt: "a dinosaur bike route in Cologne, about 24 km" },
  { glyph: "🥁", label: "Drum", category: "Objects", prompt: "a drum run in Valladolid, about 10 km" },
  { glyph: "🪶", label: "Feather", category: "Nature", prompt: "a feather run in Nantes, about 12 km" },
  { glyph: "🐸", label: "Frog", category: "Animals", prompt: "a frog run in Tampere, about 12 km" },
  { glyph: "👻", label: "Ghost", category: "Symbols", prompt: "a ghost run in Edinburgh, about 12 km" },
  { glyph: "👓", label: "Glasses", category: "Objects", prompt: "glasses GPS art in Frankfurt, running about 12 km" },
  { glyph: "🔨", label: "Hammer", category: "Objects", prompt: "a hammer bike route in Stuttgart, about 20 km" },
  { glyph: "🦔", label: "Hedgehog", category: "Animals", prompt: "a hedgehog run in Warsaw, about 14 km" },
  { glyph: "⛑️", label: "Helmet", category: "Objects", prompt: "a helmet run in Hanover, about 12 km" },
  { glyph: "🍦", label: "Ice cream", category: "Nature", prompt: "an ice cream run in Bologna, about 12 km" },
  { glyph: "🪁", label: "Kite", category: "Objects", prompt: "a kite run in Toulouse, about 12 km" },
  { glyph: "🐨", label: "Koala", category: "Animals", prompt: "a koala run in Belfast, about 14 km" },
  { glyph: "🪜", label: "Ladder", category: "Objects", prompt: "a ladder bike route in Łódź, about 20 km" },
  { glyph: "🔦", label: "Lighthouse", category: "Objects", prompt: "a lighthouse run in Porto, about 14 km" },
  { glyph: "🔒", label: "Lock", category: "Symbols", prompt: "a lock run in Düsseldorf, about 12 km" },
  { glyph: "🏅", label: "Medal", category: "Symbols", prompt: "a medal run in Seville, about 12 km" },
  { glyph: "🎤", label: "Microphone", category: "Objects", prompt: "a microphone bike route in Liverpool, about 22 km" },
  { glyph: "🐙", label: "Octopus", category: "Animals", prompt: "an octopus bike route in Thessaloniki, about 24 km" },
  { glyph: "🛩️", label: "Paper plane", category: "Objects", prompt: "a paper plane run in Eindhoven, about 12 km" },
  { glyph: "🐾", label: "Paw print", category: "Animals", prompt: "a paw print run in Cork, about 12 km" },
  { glyph: "🍕", label: "Pizza slice", category: "Objects", prompt: "a pizza slice run in Naples, about 14 km" },
  { glyph: "🥨", label: "Pretzel", category: "Objects", prompt: "a pretzel bike route in Munich, about 20 km" },
  { glyph: "🤖", label: "Robot", category: "Objects", prompt: "a robot run in Budapest, about 8 km" },
  { glyph: "✂️", label: "Scissors", category: "Objects", prompt: "scissors GPS art in Dresden, cycling about 22 km" },
  { glyph: "🐴", label: "Seahorse", category: "Animals", prompt: "a seahorse bike route in Split, about 22 km" },
  { glyph: "🛹", label: "Skateboard", category: "Objects", prompt: "a skateboard run in Malmö, about 14 km" },
  { glyph: "🐍", label: "Snake", category: "Animals", prompt: "a snake bike route in Sofia, about 22 km" },
  { glyph: "🕷️", label: "Spider", category: "Animals", prompt: "a spider bike route in Wrocław, about 24 km" },
  { glyph: "🦑", label: "Squid", category: "Animals", prompt: "a squid run in Gdańsk, about 14 km" },
  { glyph: "🩺", label: "Stethoscope", category: "Objects", prompt: "a stethoscope bike route in Geneva, about 24 km" },
  { glyph: "🚢", label: "Submarine", category: "Objects", prompt: "a submarine bike route in Helsinki, about 24 km" },
  { glyph: "🦢", label: "Swan", category: "Animals", prompt: "a swan run in Zurich, about 14 km" },
  { glyph: "⚔️", label: "Sword", category: "Objects", prompt: "a sword bike route in Belgrade, about 22 km" },
  { glyph: "🔭", label: "Telescope", category: "Objects", prompt: "a telescope bike route in Oslo, about 22 km" },
  { glyph: "⛺", label: "Tent", category: "Objects", prompt: "a tent run in Grenoble, about 12 km" },
  { glyph: "🚜", label: "Tractor", category: "Objects", prompt: "a tractor bike route in Lublin, about 22 km" },
  { glyph: "🚆", label: "Train", category: "Objects", prompt: "a train bike route in Frankfurt, about 22 km" },
  { glyph: "🌋", label: "Volcano", category: "Nature", prompt: "a volcano run in Palermo, about 14 km" },
  { glyph: "🍉", label: "Watermelon slice", category: "Nature", prompt: "a watermelon slice run in Palermo, about 14 km" },
  { glyph: "🌬️", label: "Windmill", category: "Objects", prompt: "a windmill bike route in Rotterdam, about 22 km" },
];

const HUNGARIAN_SHAPE_IDEAS = [
  { glyph: "🌶️", label: "Paprika", category: "Hungarian ideas", prompt: "a paprika run in Szeged, about 12 km" },
  { glyph: "🧊", label: "Puzzle cube", category: "Hungarian ideas", prompt: "a Rubik's cube bike route in Budapest, about 24 km" },
  { glyph: "🥸", label: "Moustache", category: "Hungarian ideas", prompt: "a moustache run in Kecskemét, about 18 km" },
  { glyph: "🍇", label: "Grape cluster", category: "Hungarian ideas", prompt: "a grape cluster bike route in Eger, about 24 km" },
  { glyph: "🍷", label: "Wine glass", category: "Hungarian ideas", prompt: "a wine glass run in Sopron, about 18 km" },
  { glyph: "🥘", label: "Cauldron", category: "Hungarian ideas", prompt: "a cauldron run in Békéscsaba, about 18 km" },
  { glyph: "🧲", label: "Horseshoe", category: "Hungarian ideas", prompt: "a horseshoe run in Debrecen, about 16 km" },
  { glyph: "🌾", label: "Wheat", category: "Hungarian ideas", prompt: "a wheat bike route in Békéscsaba, about 28 km" },
  { glyph: "🌉", label: "Suspension bridge", category: "Hungarian ideas", prompt: "a suspension bridge bike route in Budapest, about 30 km" },
  { glyph: "🗼", label: "Water tower", category: "Hungarian ideas", prompt: "a water tower bike route in Szeged, about 22 km" },
  { glyph: "🐂", label: "Grey cattle", category: "Hungarian ideas", prompt: "Hungarian grey cattle GPS art in Debrecen, cycling about 28 km" },
  { glyph: "🦌", label: "Stag", category: "Hungarian ideas", prompt: "a stag bike route in Gyöngyös, about 30 km" },
  { glyph: "🔴", label: "Pomegranate", category: "Hungarian ideas", prompt: "a pomegranate run in Pécs, about 12 km" },
  { glyph: "🧁", label: "Chimney cake", category: "Hungarian ideas", prompt: "a chimney cake bike route in Budapest, about 24 km" },
  { glyph: "♨️", label: "Thermal bath", category: "Hungarian ideas", prompt: "a thermal bath bike route in Hajdúszoboszló, about 28 km" },
  { glyph: "⛩️", label: "Folk gate", category: "Hungarian ideas", prompt: "a folk gate bike route in Szentendre, about 30 km" },
];

const QUICK_IDEAS = [...CORE_IDEAS, ...ADDITIONAL_SHAPE_IDEAS, ...HUNGARIAN_SHAPE_IDEAS];
// Choose once per page, including React's development remounts and fast reloads.
const INITIAL_EXAMPLE_PROMPT = (() => {
  let previous;
  try { previous = window.sessionStorage.getItem(EXAMPLE_PROMPT_STORAGE_KEY); } catch { /* Storage may be disabled. */ }
  const choices = QUICK_IDEAS.filter(idea => idea.prompt !== previous);
  const prompt = choices[Math.floor(Math.random() * choices.length)].prompt;
  try { window.sessionStorage.setItem(EXAMPLE_PROMPT_STORAGE_KEY, prompt); } catch { /* Random selection still works. */ }
  return prompt;
})();

const IDEA_CATEGORIES = [
  "Hungarian ideas",
  "Simple shapes",
  "Nature",
  "Animals",
  "Objects",
  "Symbols",
  "Letters, numbers & text",
];
const MAP_PLACEMENT_FALLBACK_SHAPES = [
  { id: "heart", label: "Heart" },
  { id: "star", label: "Star" },
  { id: "circle", label: "Circle" },
  { id: "diamond", label: "Diamond" },
  { id: "triangle", label: "Triangle" },
  { id: "square", label: "Square" },
  { id: "arrow", label: "Arrow" },
  { id: "flower", label: "Flower" },
  { id: "cat", label: "Cat" },
  { id: "sailboat", label: "Sailboat" },
  { id: "crown", label: "Crown" },
  { id: "dragon", label: "Dragon" },
];
const DISTINCT_IDEA_GLYPHS = Object.freeze({
  Bat: "🦇",
  Bear: "🐻",
  Bird: "🐦",
  Cat: "🐈",
  Dog: "🐕",
  Dragon: "🐉",
  Duck: "🦆",
  Owl: "🦉",
  Penguin: "🐧",
  Rabbit: "🐇",
  Turtle: "🐢",
  Whale: "🐋",
});

function ideaGlyph(idea) {
  const outlines = {
    Heart: "M12 20L4 12C-1 5 7 1 12 7C17 1 25 5 20 12Z",
    Star: "M12 2L15 9L22 10L17 15L18 22L12 18L6 22L7 15L2 10L9 9Z",
    Circle: "M12 3A9 9 0 1 0 12 21A9 9 0 1 0 12 3",
    Diamond: "M12 2L22 12L12 22L2 12Z",
    Triangle: "M12 3L22 21H2Z",
    Square: "M4 4H20V20H4Z",
  };
  if (outlines[idea.label]) return (
    <svg className="shape-symbol" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <title>{idea.glyph}</title>
      <path d={outlines[idea.label]} />
    </svg>
  );
  return DISTINCT_IDEA_GLYPHS[idea.label] ?? idea.glyph;
}

const HUNGARIAN_CITIES = [
  "Budapest",
  "Debrecen",
  "Szeged",
  "Miskolc",
  "Pécs",
  "Győr",
  "Nyíregyháza",
  "Kecskemét",
  "Székesfehérvár",
  "Szombathely",
  "Érd",
  "Szolnok",
  "Tatabánya",
  "Sopron",
  "Kaposvár",
  "Veszprém",
  "Zalaegerszeg",
  "Békéscsaba",
  "Eger",
  "Dunakeszi",
  "Nagykanizsa",
  "Hódmezővásárhely",
  "Dunaújváros",
  "Szigetszentmiklós",
  "Cegléd",
  "Vác",
  "Mosonmagyaróvár",
  "Gödöllő",
  "Baja",
  "Salgótarján",
  "Ózd",
  "Szekszárd",
  "Hajdúböszörmény",
  "Budaörs",
  "Esztergom",
  "Szentendre",
  "Kiskunfélegyháza",
  "Pápa",
  "Gyula",
  "Gyöngyös",
  "Ajka",
  "Kiskunhalas",
  "Jászberény",
  "Orosháza",
  "Szentes",
  "Gyál",
  "Hajdúszoboszló",
  "Siófok",
  "Dunaharaszti",
  "Tata",
];

// The official Lake Balaton shore-municipality list contains 45 places.
// Siófok remains in the main Hungary group above, so it is omitted here to
// keep every select value unique.
const BALATON_SHORE_CITIES = [
  "Alsóörs",
  "Aszófő",
  "Ábrahámhegy",
  "Badacsonytomaj",
  "Badacsonytördemic",
  "Balatonakali",
  "Balatonakarattya",
  "Balatonalmádi",
  "Balatonberény",
  "Balatonboglár",
  "Balatonederics",
  "Balatonfenyves",
  "Balatonfőkajár",
  "Balatonföldvár",
  "Balatonfüred",
  "Balatonfűzfő",
  "Balatongyörök",
  "Balatonkenese",
  "Balatonkeresztúr",
  "Balatonlelle",
  "Balatonmáriafürdő",
  "Balatonőszöd",
  "Balatonrendes",
  "Balatonszabadi",
  "Balatonszárszó",
  "Balatonszemes",
  "Balatonszentgyörgy",
  "Balatonszepezd",
  "Balatonudvari",
  "Balatonvilágos",
  "Csopak",
  "Fonyód",
  "Gyenesdiás",
  "Keszthely",
  "Kővágóörs",
  "Örvényes",
  "Paloznak",
  "Révfülöp",
  "Szántód",
  "Szigliget",
  "Tihany",
  "Vonyarcvashegy",
  "Zamárdi",
  "Zánka",
];

const EUROPEAN_CITIES = [
  "London",
  "Paris",
  "Berlin",
  "Madrid",
  "Rome",
  "Barcelona",
  "Vienna",
  "Amsterdam",
  "Prague",
  "Brussels",
  "Copenhagen",
  "Stockholm",
  "Oslo",
  "Helsinki",
  "Warsaw",
  "Kraków",
  "Bratislava",
  "Ljubljana",
  "Zagreb",
  "Bucharest",
  "Sofia",
  "Athens",
  "Dublin",
  "Munich",
  "Milan",
  "Lisbon",
  "Porto",
  "Zurich",
  "Tallinn",
  "Riga",
  "Birmingham",
  "Manchester",
  "Liverpool",
  "Leeds",
  "Glasgow",
  "Edinburgh",
  "Bristol",
  "Belfast",
  "Cork",
  "Marseille",
  "Lyon",
  "Toulouse",
  "Nice",
  "Nantes",
  "Strasbourg",
  "Bordeaux",
  "Lille",
  "Montpellier",
  "Grenoble",
  "Hamburg",
  "Cologne",
  "Frankfurt",
  "Stuttgart",
  "Düsseldorf",
  "Leipzig",
  "Dresden",
  "Nuremberg",
  "Hanover",
  "Bremen",
  "Valencia",
  "Seville",
  "Zaragoza",
  "Málaga",
  "Bilbao",
  "Alicante",
  "Granada",
  "Valladolid",
  "Vigo",
  "A Coruña",
  "Naples",
  "Turin",
  "Bologna",
  "Florence",
  "Genoa",
  "Palermo",
  "Bari",
  "Verona",
  "Padua",
  "Trieste",
  "Łódź",
  "Wrocław",
  "Poznań",
  "Gdańsk",
  "Szczecin",
  "Lublin",
  "Katowice",
  "Bydgoszcz",
  "Gothenburg",
  "Malmö",
  "Uppsala",
  "Bergen",
  "Trondheim",
  "Stavanger",
  "Aarhus",
  "Odense",
  "Tampere",
  "Turku",
  "Oulu",
  "Rotterdam",
  "The Hague",
  "Utrecht",
  "Eindhoven",
  "Antwerp",
  "Ghent",
  "Liège",
  "Luxembourg",
  "Salzburg",
  "Graz",
  "Innsbruck",
  "Linz",
  "Geneva",
  "Basel",
  "Bern",
  "Lausanne",
  "Brno",
  "Ostrava",
  "Košice",
  "Cluj-Napoca",
  "Timișoara",
  "Iași",
  "Varna",
  "Plovdiv",
  "Thessaloniki",
  "Patras",
  "Split",
  "Rijeka",
  "Sarajevo",
  "Belgrade",
  "Novi Sad",
  "Skopje",
  "Tirana",
  "Podgorica",
  "Pristina",
  "Vilnius",
  "Kaunas",
  "Tartu",
];

const SUGGEST_CITY_GROUPS = [
  { label: "Hungary", cities: HUNGARIAN_CITIES },
  { label: "Lake Balaton shore", cities: BALATON_SHORE_CITIES },
  { label: "Europe", cities: EUROPEAN_CITIES },
];
const SUGGEST_CITIES = [
  ...HUNGARIAN_CITIES,
  ...BALATON_SHORE_CITIES,
  ...EUROPEAN_CITIES,
];

const PROMPT_LIMIT = 320;
const PROMPT_CONTROL_CHARACTERS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u;
const PROMPT_MEANINGFUL_CHARACTER = /[\p{L}\p{N}]/u;

function normaliseRoutePrompt(value) {
  return value.normalize("NFKC").replace(/\s+/gu, " ").trim();
}

function validateRoutePrompt(value) {
  const normalised = value.normalize("NFKC");
  if (PROMPT_CONTROL_CHARACTERS.test(normalised)) {
    return {
      value: normaliseRoutePrompt(normalised),
      error: "Remove unsupported control characters from the route idea.",
    };
  }

  const cleaned = normaliseRoutePrompt(normalised);
  if (!cleaned) {
    return {
      value: cleaned,
      error: "Enter a route idea. Try ‘a heart run in Budapest, about 8 km’.",
    };
  }
  if (cleaned.length > PROMPT_LIMIT) {
    return {
      value: cleaned,
      error: `Keep the route idea to ${PROMPT_LIMIT} characters or fewer.`,
    };
  }
  if (!PROMPT_MEANINGFUL_CHARACTER.test(cleaned)) {
    return {
      value: cleaned,
      error: "Include a shape, word, letter, or number to draw.",
    };
  }
  return { value: cleaned, error: "" };
}

function validateImageReferenceUrl(value) {
  const cleaned = value.trim();
  if (!cleaned) return { value: "", error: "Enter a direct image link." };
  try {
    const parsed = new URL(cleaned);
    if (!["http:", "https:"].includes(parsed.protocol)) {
      return { value: cleaned, error: "Use a public HTTP or HTTPS image link." };
    }
    return { value: parsed.href, error: "" };
  } catch {
    return { value: cleaned, error: "Enter a complete image URL." };
  }
}

const ACTIVITY_OPTIONS = [
  ["run", "Running"],
  ["walk", "Walking"],
  ["bike", "Cycling"],
];

function activityLabel(sport) {
  return (ACTIVITY_OPTIONS.find(([value]) => value === sport) ?? ACTIVITY_OPTIONS[0])[1];
}

function distanceLimits(sport) {
  if (sport === "bike") return { minimum: 10, maximum: 200, activity: "cycling" };
  if (sport === "walk") return { minimum: 2, maximum: 40, activity: "walking" };
  return { minimum: 3, maximum: 60, activity: "running" };
}

function validateSuggestion({ city, sport, distance }) {
  const errors = {};
  if (!SUGGEST_CITIES.includes(city)) {
    errors.city = "Choose a city from the list.";
  }
  if (!ACTIVITY_OPTIONS.some(([value]) => value === sport)) {
    errors.sport = "Choose running, walking or cycling.";
  }

  const { minimum, maximum, activity } = distanceLimits(sport);
  const numericDistance = Number(distance);
  if (String(distance).trim() === "") {
    errors.distance = "Enter a distance in kilometres.";
  } else if (!Number.isFinite(numericDistance)) {
    errors.distance = "Enter the distance as a number.";
  } else if (!Number.isInteger(numericDistance)) {
    errors.distance = "Enter the distance in whole kilometres.";
  } else if (numericDistance < minimum || numericDistance > maximum) {
    errors.distance = `Enter a ${activity} distance from ${minimum} to ${maximum} km.`;
  }

  return { errors, numericDistance };
}

const GATE_COPY = {
  selected_shape: {
    label: "Drawing",
    description: "This route uses the drawing you chose.",
  },
  road_network: {
    label: "Follows connected streets",
    description: "The route uses connected roads or paths instead of straight guide lines.",
  },
  overall_score: {
    label: "Route quality",
    description: "A combined look at the drawing, distance, and start-to-finish gap.",
  },
  shape_fidelity: {
    label: "Drawing likeness",
    description: "How closely the route still looks like your drawing.",
  },
  spatial_similarity: {
    label: "Line order",
    description: "The route traces the parts of the drawing in the right order.",
  },
  coverage_similarity: {
    label: "Outline coverage",
    description: "The route covers the full drawing without skipping large sections.",
  },
  turning_similarity: {
    label: "Turns and curves",
    description: "The drawing’s distinctive corners and curves are still visible.",
  },
  landmark_similarity: {
    label: "Key points",
    description: "Important tips, corners, and notches are in the right places.",
  },
  reversal_similarity: {
    label: "No doubled-back lines",
    description: "The street route does not add U-turns that muddle the drawing.",
  },
  length_similarity: {
    label: "Extra detours",
    description: "Street detours do not add confusing extra lines to the drawing.",
  },
  extent_similarity: {
    label: "Shape proportions",
    description: "The drawing keeps roughly the same width and height.",
  },
  distance_fit: {
    label: "Requested distance",
    description: "For a requested distance, the route stays within 20% of the target.",
  },
  closure: {
    label: "Returns to the start",
    description: "For a loop, the finish is close to the starting point.",
  },
};

function formatMetric(value, digits = 2) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "n/a";
}

function formatPercent(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "n/a";
}

function formatSigned(value, digits = 2, suffix = "") {
  if (typeof value !== "number" || !Number.isFinite(value)) return "n/a";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
}

function formatGateValue(gate) {
  if (!gate?.applies) return "Not required";
  if (typeof gate.value === "boolean") return gate.value ? "Yes" : "No";
  if (typeof gate.value === "number") return formatPercent(gate.value);
  return normaliseLabel(gate.value);
}

function formatGateMinimum(gate) {
  if (!gate?.applies || typeof gate.minimum === "boolean") return "";
  if (typeof gate.minimum === "number") return `target ${formatPercent(gate.minimum)}`;
  return gate.minimum ? `target: ${normaliseLabel(gate.minimum)}` : "";
}

function explainGateResult(gate) {
  if (!gate?.applies) return "Not needed for this route.";
  if (typeof gate.value === "boolean") {
    return gate.passed
      ? "Looks good."
      : "We couldn’t confirm this. Check the map before using the route.";
  }
  if (typeof gate.value === "number" && typeof gate.minimum === "number") {
    const difference = Math.round(Math.abs(gate.value - gate.minimum) * 100);
    return gate.passed
      ? `${difference} point${difference === 1 ? "" : "s"} above the target.`
      : `${difference} point${difference === 1 ? "" : "s"} below the target. Check how it looks on the map.`;
  }
  return gate.passed
    ? "Matches your choice."
    : "Doesn’t match your choice.";
}

function normaliseLabel(value) {
  if (!value) return "n/a";
  return String(value)
    .replaceAll("_", " ")
    .replace(/(^|[\s-])(\p{L})/gu, (_, prefix, letter) => `${prefix}${letter.toUpperCase()}`);
}

function safeFilePart(value) {
  const cleaned = String(value || "gps-art-route")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .slice(0, 48);
  return cleaned || "gps-art-route";
}

function saveFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function readGalleryRemovalTokens() {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(GALLERY_REMOVAL_STORAGE_KEY) ?? "{}",
    );
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(
      Object.entries(stored).filter(
        ([publicId, token]) => publicId && typeof token === "string" && token,
      ),
    );
  } catch {
    return {};
  }
}

function rememberGalleryRemovalToken(publicId, token) {
  const next = { ...readGalleryRemovalTokens(), [publicId]: token };
  try {
    window.localStorage.setItem(GALLERY_REMOVAL_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A private-browsing/storage failure must not turn a successful upload
    // into a false publication error or encourage a duplicate retry.
  }
  return next;
}

function forgetGalleryRemovalToken(publicId) {
  const next = { ...readGalleryRemovalTokens() };
  delete next[publicId];
  try {
    window.localStorage.setItem(GALLERY_REMOVAL_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // The server deletion still succeeded; keep the UI truthful for this session.
  }
  return next;
}

function mergeGalleryAssets(current, received, { replace, publishedAsset, removedIds }) {
  const visibleReceived = (Array.isArray(received) ? received : []).filter(
    (asset) => asset?.id && !removedIds.has(asset.id),
  );
  let next = replace ? visibleReceived : [...current, ...visibleReceived];
  if (publishedAsset?.id && !removedIds.has(publishedAsset.id)) {
    next = [publishedAsset, ...next.filter((asset) => asset.id !== publishedAsset.id)];
  }
  const seen = new Set();
  return next.filter((asset) => {
    if (!asset?.id || seen.has(asset.id) || removedIds.has(asset.id)) return false;
    seen.add(asset.id);
    return true;
  });
}

function chooseFeaturedGalleryAsset(assets) {
  const available = (Array.isArray(assets) ? assets : []).filter(
    (asset) => asset?.id && typeof asset.image_url === "string" && asset.image_url.startsWith("https://"),
  );
  if (available.length === 0) return null;
  let previousId = null;
  try {
    previousId = window.localStorage.getItem(FEATURED_GALLERY_STORAGE_KEY);
  } catch {
    // Private browsing still gets a random gallery image.
  }
  const choices = available.length > 1
    ? available.filter((asset) => asset.id !== previousId)
    : available;
  const selected = choices[Math.floor(Math.random() * choices.length)];
  try {
    window.localStorage.setItem(FEATURED_GALLERY_STORAGE_KEY, selected.id);
  } catch {
    // The image remains usable even if browser storage is disabled.
  }
  return selected;
}

function sampleControlPoints(points, maximum = 18) {
  const valid = (Array.isArray(points) ? points : []).filter(
    (point) =>
      Array.isArray(point) &&
      Number.isFinite(point[0]) &&
      Number.isFinite(point[1]),
  );
  if (valid.length <= maximum) return valid.map((point) => [...point]);
  const indices = Array.from(
    { length: maximum },
    (_, index) => Math.round((index * (valid.length - 1)) / (maximum - 1)),
  );
  return [...new Set(indices)].map((index) => [...valid[index]]);
}

function MetricCard({ label, value, detail, tone = "neutral" }) {
  return (
    <div className={`metric metric--${tone}`}>
      <dt>{label}</dt>
      <dd>{value}</dd>
      {detail && <dd className="metric-detail">{detail}</dd>}
    </div>
  );
}

function RouteReadinessCard({
  readiness,
  roadRouted,
  activeConcernCode = null,
  onConcernSelect,
}) {
  const data = readiness && typeof readiness === "object" ? readiness : {};
  const concerns = Array.isArray(data.concerns) ? data.concerns : [];
  const surfaces = Array.isArray(data.surfaces) ? data.surfaces.slice(0, 6) : [];
  const status = ["ready", "review"].includes(data.status)
    ? data.status
    : "unavailable";
  const statusCopy = {
    ready: "Looks clear",
    review: "Check the route",
    unavailable: "Limited data",
  }[status];
  const grade = Number.isFinite(data.max_grade_percent)
    ? `${data.max_grade_percent.toFixed(1)}%${data.max_grade_is_lower_bound ? "+" : ""}`
    : "n/a";
  const elevationGain = Number.isFinite(data.elevation_gain_m)
    ? `${Math.round(data.elevation_gain_m)} m`
    : "n/a";
  const surfaceKnown = Number.isFinite(data.surface_known_share)
    ? formatPercent(data.surface_known_share)
    : "n/a";
  const highlightedConcernCount = concerns.reduce(
    (count, concern) => count + (concern.segments_preview?.length ?? 0),
    0,
  );

  return (
    <section
      className={`readiness-card readiness-card--${status}`}
      aria-labelledby="readiness-title"
    >
      <div className="readiness-heading">
        <div>
          <span className="eyebrow">Before you go</span>
          <h3 id="readiness-title">Route readiness</h3>
        </div>
        <span className={`readiness-status readiness-status--${status}`}>
          {statusCopy}
        </span>
      </div>

      <p className="readiness-summary">
        {!roadRouted || status === "unavailable"
          ? "A street-matched route is needed for elevation, surface, and segment checks."
          : status === "review"
            ? highlightedConcernCount > 0
              ? "Review the highlighted sections before heading out."
              : "Some route details need a closer check before heading out."
            : "The available map data has no obvious route-readiness flags."}
      </p>

      <dl className="readiness-metrics">
        <div>
          <dt>Elevation gain</dt>
          <dd>{elevationGain}</dd>
        </div>
        <div>
          <dt>Steepest climb</dt>
          <dd>{grade}</dd>
        </div>
        <div>
          <dt>Surface known</dt>
          <dd>{surfaceKnown}</dd>
        </div>
      </dl>

      {surfaces.length > 0 && (
        <div className="surface-breakdown">
          <div className="readiness-subheading">
            <strong>Surface mix</strong>
            {Number.isFinite(data.unpaved_share) && (
              <span>{formatPercent(data.unpaved_share)} unpaved</span>
            )}
          </div>
          <div className="surface-bar" aria-label="Route surface composition">
            {surfaces.map((surface) => (
              <span
                key={surface.code}
                className={`surface-bar-part surface-bar-part--${surface.category}`}
                style={{ width: `${Math.max(1, Math.round((surface.share ?? 0) * 100))}%` }}
                title={`${surface.label}: ${formatPercent(surface.share)}`}
              />
            ))}
          </div>
          <ul className="surface-legend">
            {surfaces.map((surface) => (
              <li key={surface.code}>
                <span
                  className={`surface-key surface-key--${surface.category}`}
                  aria-hidden="true"
                />
                <span>{surface.label}</span>
                <strong>{formatPercent(surface.share)}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="readiness-concerns">
        <div className="readiness-subheading">
          <strong>Sections to check</strong>
          {activeConcernCode ? (
            <button
              type="button"
              className="concern-reset"
              onClick={() => onConcernSelect?.(null)}
            >
              Show full route
            </button>
          ) : (
            <span>
              {concerns.length > 0
                ? `${concerns.length} item${concerns.length === 1 ? "" : "s"}`
                : "No items"}
            </span>
          )}
        </div>
        {concerns.length > 0 ? (
          <ul>
            {concerns.map((concern) => (
              <li key={concern.code} className={`concern--${concern.severity}`}>
                <button
                  type="button"
                  className="concern-button"
                  aria-pressed={activeConcernCode === concern.code}
                  onClick={() => onConcernSelect?.(
                    activeConcernCode === concern.code ? null : concern.code,
                  )}
                >
                  <span className="concern-icon" aria-hidden="true">
                    {concern.severity === "warning" ? "!" : "i"}
                  </span>
                  <span>
                    <strong>{concern.label}</strong>
                    <small>{concern.detail}</small>
                  </span>
                  <span className="concern-distance">
                    {concern.distance_m >= 1_000
                      ? `${formatMetric(concern.distance_m / 1_000, 1)} km`
                      : `${Math.round(concern.distance_m)} m`}
                    <small>View on map</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="readiness-empty">
            {status === "unavailable"
              ? "No segment data is available for this preview."
              : "No sections were flagged in the available map data."}
          </p>
        )}
      </div>

      <p className="readiness-note">
        Based on routing and OpenStreetMap data. Check current closures, access rules, traffic,
        and weather separately.
      </p>
    </section>
  );
}

function StreetCanvasCard({ candidates = [] }) {
  const best = candidates[0];
  if (!best) return null;
  return (
    <section className="street-canvas-card" aria-labelledby="street-canvas-title">
      <div className="readiness-heading">
        <div>
          <span className="eyebrow">Street fit search</span>
          <h3 id="street-canvas-title">Best nearby areas</h3>
        </div>
        <span className="readiness-status readiness-status--ready">
          {formatPercent(best.readability_score)} fit
        </span>
      </div>
      <p>
        These are the strongest nearby street-network matches before full route routing.
      </p>
      <ol className="street-canvas-list">
        {candidates.slice(0, 4).map((candidate) => (
          <li key={`${candidate.rank}-${candidate.rotation_deg}-${candidate.scale_m}`}>
            <strong>Area {candidate.rank}</strong>
            <span>{formatPercent(candidate.readability_score)} readable</span>
            <small>
              {formatPercent(candidate.snap_coverage)} street support, {formatMetric(candidate.snap_distance_m, 0)} m average snap
            </small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TimedReadinessCard({ points }) {
  const [departure, setDeparture] = useState("");
  const [briefing, setBriefing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const check = async () => {
    if (!departure || !Array.isArray(points) || points.length < 1) return;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setBusy(true);
    setError("");
    try {
      const response = await requestTimedReadiness({
        latitude: points[0][0], longitude: points[0][1], departure_at: new Date(departure).toISOString(),
      });
      if (requestSequence.current === requestId) setBriefing(response);
    } catch (requestError) {
      if (requestSequence.current === requestId) {
        setError(requestError.message || "We couldn’t check the time-based route context.");
      }
    } finally {
      if (requestSequence.current === requestId) setBusy(false);
    }
  };
  const forecastTime = briefing?.weather?.forecast_at
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
      .format(new Date(briefing.weather.forecast_at))
    : null;
  return (
    <section className="timed-readiness-card" aria-labelledby="timed-readiness-title">
      <div>
        <span className="eyebrow">When you go</span>
        <h3 id="timed-readiness-title">Time-aware check</h3>
      </div>
      <div className="timed-readiness-controls">
        <label>
          Departure
          <input
            type="datetime-local"
            value={departure}
            onChange={(event) => {
              requestSequence.current += 1;
              setDeparture(event.target.value);
              setBriefing(null);
              setError("");
              setBusy(false);
            }}
          />
        </label>
        <button type="button" className="button button--secondary" onClick={check} disabled={!departure || busy}>
          {busy ? "Checking..." : "Check conditions"}
        </button>
      </div>
      {briefing && (
        <div className="timed-readiness-result" role="status">
          <strong>{briefing.daylight === "daylight" ? "Daylight expected." : "After dark at this time."}</strong>
          {briefing.weather ? (
            <>
              <span>Forecast for {forecastTime}.</span>
              <span>
                {briefing.weather.temperature_c == null ? "Temperature unavailable" : `${Math.round(briefing.weather.temperature_c)}°C`}
                {briefing.weather.wind_kph != null && ` · ${Math.round(briefing.weather.wind_kph)} km/h wind`}
                {briefing.weather.precipitation_mm != null && ` · ${briefing.weather.precipitation_mm} mm precipitation`}.
              </span>
            </>
          ) : (
            <span>{briefing.weather_message || "Hourly weather is unavailable for this departure."}</span>
          )}
        </div>
      )}
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>Weather is matched to the selected hour when it is inside the forecast window. Closures and access rules still need a local check.</small>
    </section>
  );
}

function formatOccasionDate(isoDate) {
  const date = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) return isoDate;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function browserRegionHints() {
  if (typeof window === "undefined") return {};
  let timezone = "";
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    // Region hints are optional; the API can also use deployment headers.
  }
  return {
    timezone,
    locale: navigator.language || "",
  };
}

function OccasionStrip({ disabled, onPick, city }) {
  const [items, setItems] = useState([]);
  const [destinations, setDestinations] = useState([]);
  const [regionLabel, setRegionLabel] = useState("");
  useEffect(() => {
    let cancelled = false;
    fetchOccasions({ daysAhead: 120, ...browserRegionHints() })
      .then((response) => {
        if (!cancelled) {
          setItems((response?.occasions ?? []).slice(0, 3));
          setRegionLabel(response?.country_name || "your region");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItems([]);
          setRegionLabel("");
        }
      });
    fetchDestinations()
      .then((response) => {
        if (!cancelled) setDestinations(response?.destinations ?? []);
      })
      .catch(() => {
        if (!cancelled) setDestinations([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const foldCity = (value) =>
    String(value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .trim()
      .toLowerCase();
  const cityKey = foldCity(city);
  const cityPicks = cityKey
    ? destinations.filter((entry) => foldCity(entry.city) === cityKey).slice(0, 3)
    : [];
  if (!items.length && !cityPicks.length) return null;
  return (
    <section aria-label="Route inspiration">
    <details className="inspiration-panel">
      <summary>
        <span>
          <strong>Route inspiration</strong>
          <small>
            {cityPicks.length > 0
              ? `${cityPicks.length} city pick${cityPicks.length === 1 ? "" : "s"} for ${city}`
              : `Seasonal ideas for ${regionLabel || "your region"}`}
            {cityPicks.length > 0 && items.length > 0
              ? ` · ${items.length} upcoming near ${regionLabel || "you"}`
              : ""}
          </small>
        </span>
        <b aria-hidden="true">+</b>
      </summary>
      <div className="inspiration-groups">
        {cityPicks.length > 0 && (
          <section className="inspiration-group inspiration-group--city" aria-labelledby="city-picks-title">
            <div className="inspiration-group-heading">
              <span className="eyebrow" id="city-picks-title">City picks</span>
              <small>Made for {city}</small>
            </div>
            <ul>
              {cityPicks.map((pick) => (
                <li key={`${pick.city}-${pick.shape_prompt}`}>
                  <button
                    type="button"
                    className="occasion-chip city-pick-chip"
                    onClick={() => onPick(pick)}
                    disabled={disabled}
                    title={pick.blurb}
                  >
                    <strong>{pick.name}</strong>
                    <small>
                      {pick.city}
                      {pick.partner_ready ? " · official" : ""}
                      {pick.sport ? ` · ${activityLabel(pick.sport)}` : ""}
                      {Number.isFinite(pick.distance_km) ? ` · ${pick.distance_km} km` : ""}
                    </small>
                    <em>{normaliseLabel(pick.shape_prompt)}</em>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {items.length > 0 && (
          <section className="inspiration-group" aria-labelledby="occasions-title">
            <div className="inspiration-group-heading">
              <span className="eyebrow" id="occasions-title">Upcoming occasions</span>
              <small>Relevant near {regionLabel || "you"}</small>
            </div>
          <ul>
            {items.map((occasion) => (
              <li key={occasion.id}>
                <button
                  type="button"
                  className="occasion-chip"
                  onClick={() => onPick(occasion)}
                  disabled={disabled}
                  title={occasion.detail}
                >
                  <strong>{occasion.name}</strong>
                  <small>
                    {formatOccasionDate(occasion.date)}
                    {" · "}
                    {occasion.days_until === 0
                      ? "today"
                      : occasion.days_until === 1
                        ? "tomorrow"
                        : `in ${occasion.days_until} days`}
                    {occasion.sport ? ` · ${activityLabel(occasion.sport)}` : ""}
                    {Number.isFinite(occasion.distance_km) ? ` · ${occasion.distance_km} km` : ""}
                  </small>
                  <em>{normaliseLabel(occasion.shape_prompt)}</em>
                </button>
              </li>
            ))}
          </ul>
          </section>
        )}
      </div>
    </details>
    </section>
  );
}

function normalisePlacementRotation(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return ((numeric + 180) % 360 + 360) % 360 - 180;
}

function ShapePlacementOverlay({ open, preview, onClose, onConfirm, busy }) {
  const dialogRef = useModalDialog(open, onClose);
  const [center, setCenter] = useState(preview?.center ?? [47.4979, 19.0402]);
  const [scaleM, setScaleM] = useState(preview?.scale_m ?? 2_000);
  const [rotationDeg, setRotationDeg] = useState(
    normalisePlacementRotation(preview?.rotation_deg ?? 0),
  );
  const [searchRadiusM, setSearchRadiusM] = useState(900);

  useEffect(() => {
    if (!open || !preview) return;
    setCenter(preview.center);
    setScaleM(preview.scale_m);
    setRotationDeg(normalisePlacementRotation(preview.rotation_deg));
    setSearchRadiusM(900);
  }, [open, preview]);

  if (!open || !preview) return null;
  const scaleStep = 50;
  const minimumScale = Math.max(
    100,
    Math.floor((preview.scale_m * 0.45) / scaleStep) * scaleStep,
  );
  const maximumScale = Math.min(
    50_000,
    Math.ceil((preview.scale_m * 1.8) / scaleStep) * scaleStep,
  );

  return (
    <div
      className="shape-placement-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="shape-placement-title"
      ref={dialogRef}
      tabIndex={-1}
    >
      <section className="shape-placement-sheet">
        <header className="shape-placement-heading">
          <div>
            <span className="eyebrow">Manual map placement</span>
            <h2 id="shape-placement-title">Position the {preview.label.toLowerCase()}</h2>
            <p>Click the map or drag the centre handle. The planner will test nearby streets.</p>
          </div>
          <button
            type="button"
            className="button button--quiet shape-placement-close"
            onClick={onClose}
            aria-label="Close shape placement map"
            data-dialog-initial-focus
          >
            ×
          </button>
        </header>

        <div className="shape-placement-workspace">
          <div className="shape-placement-map-shell">
            <Suspense fallback={<div className="shape-placement-map-fallback">Loading map…</div>}>
              <ShapePlacementMap
                paths={preview.paths}
                center={center}
                cityBbox={preview.city_bbox}
                scaleM={scaleM}
                rotationDeg={rotationDeg}
                shapeLabel={preview.label.toLowerCase()}
                onCenterChange={setCenter}
              />
            </Suspense>
            <p className="shape-placement-map-hint">
              Approximate centre: {center[0].toFixed(4)}, {center[1].toFixed(4)}
            </p>
          </div>

          <aside className="shape-placement-controls" aria-label="Shape placement controls">
            <div className="shape-placement-selection">
              <strong>{preview.label}</strong>
              <span>{preview.city} · {activityLabel(preview.sport)} · {preview.distance_km} km</span>
            </div>
            <label htmlFor="placement-size">
              Footprint size
              <output htmlFor="placement-size">{(scaleM / 1_000).toFixed(1)} km</output>
            </label>
            <input
              id="placement-size"
              type="range"
              min={minimumScale}
              max={maximumScale}
              step={scaleStep}
              value={scaleM}
              onChange={(event) => setScaleM(Number(event.target.value))}
            />
            <small>Larger footprints usually produce longer, simpler street segments.</small>

            <label htmlFor="placement-rotation">
              Rotation
              <output htmlFor="placement-rotation">{Math.round(rotationDeg)}°</output>
            </label>
            <input
              id="placement-rotation"
              type="range"
              min="-180"
              max="180"
              step="1"
              value={rotationDeg}
              onChange={(event) => setRotationDeg(Number(event.target.value))}
            />
            <small>Turn the drawing to follow the dominant street direction.</small>

            <label htmlFor="placement-search-radius">
              Nearby fine-tuning
              <output htmlFor="placement-search-radius">±{Math.round(searchRadiusM)} m</output>
            </label>
            <input
              id="placement-search-radius"
              type="range"
              min="200"
              max="2000"
              step="100"
              value={searchRadiusM}
              onChange={(event) => setSearchRadiusM(Number(event.target.value))}
            />
            <small>The route search stays close to your chosen position.</small>

            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                setCenter(preview.center);
                setScaleM(preview.scale_m);
                setRotationDeg(normalisePlacementRotation(preview.rotation_deg));
                setSearchRadiusM(900);
              }}
              disabled={busy}
            >
              Reset placement
            </button>
          </aside>
        </div>

        <div className="shape-placement-actions">
          <p>The orange line is a guide. The generated GPX will follow connected streets.</p>
          <div>
            <button type="button" className="button button--quiet" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="button button--primary"
              onClick={() =>
                onConfirm({
                  center_lat: Number(center[0].toFixed(6)),
                  center_lon: Number(center[1].toFixed(6)),
                  scale_m: Number(scaleM),
                  rotation_deg: Number(rotationDeg),
                  search_radius_m: Number(searchRadiusM),
                })
              }
              disabled={busy}
            >
              {busy ? "Starting route search…" : "Fit to streets and create GPX"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function NightReadinessCard({ points, overlayType, onOverlayChange }) {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const active = overlayType === "night";
  const check = async () => {
    if (!Array.isArray(points) || points.length < 2) return;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setBusy(true);
    setError("");
    try {
      const response = await requestNightReadiness({ points });
      if (requestSequence.current === requestId) setReport(response);
    } catch (requestError) {
      if (requestSequence.current === requestId) {
        setError(requestError.message || "We couldn’t fetch street-lighting data.");
      }
    } finally {
      if (requestSequence.current === requestId) setBusy(false);
    }
  };
  const toggleOverlay = () => {
    if (!report) return;
    onOverlayChange(
      active
        ? null
        : {
            type: "night",
            segments: report.concerns
              .filter((concern) => concern.segments_preview?.length > 1)
              .map((concern) => ({
                id: concern.code,
                label: concern.label,
                kind: "dark",
                points_preview: concern.segments_preview,
              })),
          },
    );
  };
  const share = (value) => (Number.isFinite(value) ? formatPercent(value) : "n/a");
  return (
    <section className="night-card" aria-labelledby="night-title">
      <div>
        <span className="eyebrow">After dark</span>
        <h3 id="night-title">Night-run check</h3>
        <p>Street lighting and traffic exposure from OpenStreetMap tags.</p>
      </div>
      <div className="mural-controls">
        <button type="button" className="button button--secondary" onClick={check} disabled={busy || !points.length}>
          {busy ? "Checking..." : report ? "Re-check" : "Check lighting"}
        </button>
      </div>
      {report && report.available && (
        <>
          <dl className="readiness-metrics">
            <div>
              <dt>Lit streets</dt>
              <dd>{share(report.lit_share)}</dd>
            </div>
            <div>
              <dt>Unlit</dt>
              <dd>{share(report.unlit_share)}</dd>
            </div>
            <div>
              <dt>Traffic</dt>
              <dd>{normaliseLabel(report.traffic_label)}</dd>
            </div>
          </dl>
          {report.message && <p className="mural-note">{report.message}</p>}
          {(report.concerns ?? []).length > 0 && (
            <button type="button" className="button button--secondary" aria-pressed={active} onClick={toggleOverlay}>
              {active ? "Hide unlit sections" : "Show unlit sections on map"}
            </button>
          )}
        </>
      )}
      {report && !report.available && <p className="mural-note">{report.message}</p>}
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>Tags are volunteer-maintained and can be incomplete. A headlamp still beats a map layer.</small>
    </section>
  );
}

function LandmarksCard({ points, onMarkersChange }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  useEffect(() => () => onMarkersChange?.([]), [onMarkersChange]);
  const find = async () => {
    if (!Array.isArray(points) || points.length < 4) return;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setBusy(true);
    setError("");
    try {
      const response = await requestRouteLandmarks({ points });
      if (requestSequence.current !== requestId) return;
      setResult(response);
      onMarkersChange?.(response.available ? response.landmarks : []);
    } catch (requestError) {
      if (requestSequence.current === requestId) {
        setError(requestError.message || "We couldn’t look for sights.");
      }
    } finally {
      if (requestSequence.current === requestId) setBusy(false);
    }
  };
  return (
    <section className="landmarks-card" aria-labelledby="landmarks-title">
      <div>
        <span className="eyebrow">Sightseeing</span>
        <h3 id="landmarks-title">Sights along the route</h3>
        <p>Named attractions within about 90 m of the line, in running order.</p>
      </div>
      <div className="mural-controls">
        <button type="button" className="button button--secondary" onClick={find} disabled={busy || !points.length}>
          {busy ? "Looking..." : result ? "Look again" : "Find sights"}
        </button>
      </div>
      {result && result.available && result.landmarks.length > 0 && (
        <ol className="landmarks-list">
          {result.landmarks.map((landmark) => (
            <li key={`${landmark.name}-${landmark.offset_km}`}>
              <span>{landmark.name}</span>
              <small>
                {formatMetric(landmark.offset_km, 1)} km · {normaliseLabel(landmark.kind)}
              </small>
            </li>
          ))}
        </ol>
      )}
      {result && result.available && result.landmarks.length === 0 && (
        <p className="mural-note">{result.message}</p>
      )}
      {result && !result.available && <p className="mural-note">{result.message}</p>}
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>Sights come from OpenStreetMap tourism and historic tags.</small>
    </section>
  );
}

function LessonPackCard({ activeRoute, shapeName, city, onOpen }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const guidePoints = Array.isArray(activeRoute.ideal_preview)
    ? activeRoute.ideal_preview
    : (Array.isArray(activeRoute.points_preview) ? activeRoute.points_preview : []);
  const build = async () => {
    if (guidePoints.length < 3) return;
    setBusy(true);
    setError("");
    try {
      const pack = await buildLessonPack({
        reference_points: guidePoints,
        closed: Boolean(activeRoute.closed),
        title: `${shapeName} walk in ${city}`.slice(0, 80),
        shape_name: String(shapeName || "drawing").slice(0, 60),
      });
      if (!pack?.available) {
        setError(pack?.message || "This drawing can’t be turned into a worksheet yet.");
        return;
      }
      onOpen(pack, guidePoints);
    } catch (requestError) {
      setError(requestError.message || "We couldn’t build the worksheet.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="lesson-card" aria-labelledby="lesson-title">
      <div>
        <span className="eyebrow">Classroom</span>
        <h3 id="lesson-title">Geometry worksheet</h3>
        <p>
          Turn this drawing into a printable walk: lettered waypoints, compass bearings, and leg
          distances for a class exercise.
        </p>
      </div>
      <div className="mural-controls">
        <button
          type="button"
          className="button button--secondary"
          onClick={build}
          disabled={busy || guidePoints.length < 3}
        >
          {busy ? "Building…" : "Build worksheet"}
        </button>
      </div>
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>Bearings come from the same guide the planner routes over, so the walk matches the map.</small>
    </section>
  );
}

function ArtRescueCard({ activeRoute, shapeName, city, sport, overlayType, onOverlayChange, group = false }) {
  const titleId = useId();
  const [files, setFiles] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = overlayType === "rescue";
  const requestRef = useRef(null);
  useEffect(() => () => requestRef.current?.abort(), []);
  const plannedPoints = Array.isArray(activeRoute.points_preview) ? activeRoute.points_preview : [];
  const compare = async () => {
    if (!files.length || plannedPoints.length < 4) return;
    setBusy(true);
    setError("");
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const recordings = [];
      for (const file of files) {
        recordings.push({ name: file.name, gpx: await file.text() });
      }
      const response = await rescueArtwork({
        planned_points: plannedPoints,
        recordings,
        tolerance_m: 25,
        name: (city ? `${shapeName} in ${city}` : shapeName).slice(0, 100),
        sport: sport === "bike" ? "bike" : "run",
      }, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setAnalysis(response);
      onOverlayChange(null);
    } catch (requestError) {
      if (!controller.signal.aborted) setError(requestError.message || "We couldn’t compare those recordings.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };
  const toggleMissing = () => {
    if (!analysis) return;
    onOverlayChange(
      active
        ? null
        : {
            type: "rescue",
            segments: analysis.missing_segments.map((segment) => ({
              id: segment.id,
              label: segment.label,
              kind: "missing",
              points_preview: segment.points_preview,
            })),
          },
    );
  };
  return (
    <section className="art-rescue-card" aria-labelledby={titleId}>
      <div>
        <span className="eyebrow">{group ? "Finish the mural" : "Multi-session drawing"}</span>
        <h3 id={titleId}>{group ? "Combine finished runs" : "Finish your drawing"}</h3>
        <p>
          {group ? "Upload the group’s GPX files" : "Upload GPX files from your outings"} to see how much of the drawing is done and plan the missing parts. Up to 24 files, 2 MB each.
        </p>
      </div>
      <div className="mural-controls art-rescue-controls">
        <label className="art-rescue-file-label">
          Recordings
          <input
            type="file"
            multiple
            disabled={busy}
            accept=".gpx,application/gpx+xml,text/xml"
            onChange={(event) => {
              const selected = Array.from(event.target.files ?? []);
              setAnalysis(null);
              onOverlayChange(null);
              const invalid = selected.length > 24 || selected.some(file => file.size > 2_000_000);
              setFiles(invalid ? [] : selected);
              setError(invalid ? "Choose at most 24 GPX files, each no larger than 2 MB." : "");
            }}
          />
        </label>
        <button
          type="button"
          className="button button--secondary"
          onClick={compare}
          disabled={busy || files.length === 0 || plannedPoints.length < 4}
        >
          {busy ? "Comparing..." : "Compare recordings"}
        </button>
        {busy && <button type="button" className="button button--quiet" onClick={() => {
          requestRef.current?.abort(); setBusy(false);
        }}>Cancel comparison</button>}
      </div>
      {files.length > 0 && (
        <p className="mural-note">
          {files.length} file{files.length === 1 ? "" : "s"} ready; analysed in memory, never stored.
        </p>
      )}
      {analysis && (
        <>
          <dl className="readiness-metrics">
            <div>
              <dt>Covered</dt>
              <dd>{formatPercent(analysis.coverage)}</dd>
            </div>
            <div>
              <dt>On the drawing</dt>
              <dd>{formatPercent(analysis.precision)}</dd>
            </div>
            <div>
              <dt>Art match</dt>
              <dd>{formatPercent(analysis.art_match)}</dd>
            </div>
          </dl>
          <p className="mural-note">{analysis.message}</p>
          <p className="mural-note">{analysis.authenticity}</p>
          <div className="download-actions art-rescue-actions">
            {analysis.merged_recording_gpx && (
              <button
                type="button"
                className="button button--secondary"
                onClick={() => saveFile(`${safeFilePart(`${shapeName}-${city}`)}-combined.gpx`, analysis.merged_recording_gpx, "application/gpx+xml")}
              >
                Combined street GPX
              </button>
            )}
            {analysis.missing_ink_gpx && (
              <button
                type="button"
                className="button button--secondary"
                onClick={() => saveFile(`${safeFilePart(`${shapeName}-${city}`)}-missing-ink.gpx`, analysis.missing_ink_gpx, "application/gpx+xml")}
              >
                Missing-ink mission
              </button>
            )}
            {analysis.missing_segments.length > 0 && (
              <button type="button" className="button button--secondary" aria-pressed={active} onClick={toggleMissing}>
                {active ? "Hide missing parts" : "Show missing parts on map"}
              </button>
            )}
          </div>
          <ul className="mural-sections">
            {analysis.missing_segments.map(segment => <li key={segment.id}>
              <span>{segment.label}: {formatMetric(segment.distance_m / 1000)} km</span>
              <button type="button" className="button button--secondary" onClick={() => saveFile(`${safeFilePart(segment.label)}.gpx`, segment.gpx, "application/gpx+xml")}>GPX for {segment.label}</button>
            </li>)}
          </ul>
        </>
      )}
      {error && <p className="editor-error" role="alert">{error}</p>}
    </section>
  );
}

const DIALOG_FOCUSABLE = [
  "button:not(:disabled)",
  "a[href]",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  "summary",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function visibleDialogControls(dialog) {
  return [...(dialog?.querySelectorAll(DIALOG_FOCUSABLE) ?? [])].filter(
    (element) => element.tabIndex >= 0 && element.getClientRects().length > 0
      && getComputedStyle(element).visibility !== "hidden" && !element.closest("[inert]"),
  );
}

function inertDialogBackground(dialog) {
  const outside = [];
  let branch = dialog;
  while (branch && branch !== document.body) {
    const parent = branch.parentElement;
    if (!parent) break;
    for (const sibling of parent.children) {
      if (sibling !== branch && !sibling.inert) {
        sibling.inert = true;
        outside.push(sibling);
      }
    }
    branch = parent;
  }
  return () => { for (const element of outside) element.inert = false; };
}

function useModalDialog(open, onClose, bodyClassName = null) {
  const dialogRef = useRef(null);
  const previousFocusRef = useRef(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (bodyClassName) document.body.classList.add(bodyClassName);
    const restoreBackground = inertDialogBackground(dialogRef.current);

    const focusInitialControl = () => {
      const dialog = dialogRef.current;
      const target =
        dialog?.querySelector("[data-dialog-initial-focus]") ||
        visibleDialogControls(dialog)[0] ||
        dialog;
      target?.focus({ preventScroll: true });
    };
    const focusFrame = window.requestAnimationFrame(focusInitialControl);

    const handleKeyDown = (event) => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current?.();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = visibleDialogControls(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      restoreBackground();
      if (bodyClassName) document.body.classList.remove(bodyClassName);
      if (previousFocusRef.current?.isConnected) previousFocusRef.current.focus();
    };
  }, [bodyClassName, open]);

  return dialogRef;
}

function GiftPosterOverlay({ open, imageDataUrl, stats, dedication, onDedicationChange, onClose }) {
  const dialogRef = useModalDialog(open, onClose, "poster-print-open");
  if (!open) return null;
  return (
    <div
      className="poster-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Gift poster preview"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="poster-sheet">
        <label className="poster-dedication">
          Dedication
          <input
            data-dialog-initial-focus
            type="text"
            value={dedication}
            maxLength={80}
            placeholder="For Ada, every street a heartbeat"
            onChange={(event) => onDedicationChange(event.target.value)}
          />
        </label>
        {dedication && <p className="poster-dedication-print poster-title">{dedication}</p>}
        {imageDataUrl ? (
          <img src={imageDataUrl} alt="The finished GPS artwork on its street map" />
        ) : (
          <p>The map image could not be captured.</p>
        )}
        <dl className="poster-stats">
          {stats.map(([term, value]) => (
            <div key={term}>
              <dt>{term}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <p className="poster-footer">
          Planned with Paceasso · Map data © OpenStreetMap contributors
        </p>
        <div className="poster-actions">
          <button type="button" className="button button--primary" onClick={() => window.print()}>
            Print
          </button>
          <button type="button" className="button button--secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function AccessibilityCard({ points, overlayType, onOverlayChange }) {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const active = overlayType === "barrier";
  const check = async () => {
    if (!Array.isArray(points) || points.length < 2) return;
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setBusy(true);
    setError("");
    try {
      const response = await requestAccessibilityReadiness({ points });
      if (requestSequence.current === requestId) setReport(response);
    } catch (requestError) {
      if (requestSequence.current === requestId) {
        setError(requestError.message || "We couldn’t fetch accessibility data.");
      }
    } finally {
      if (requestSequence.current === requestId) setBusy(false);
    }
  };
  const toggleOverlay = () => {
    if (!report) return;
    onOverlayChange(
      active
        ? null
        : {
            type: "barrier",
            segments: report.concerns
              .filter((concern) => concern.segments_preview?.length > 1)
              .map((concern) => ({
                id: concern.code,
                label: concern.label,
                kind: "dark",
                points_preview: concern.segments_preview,
              })),
          },
    );
  };
  const share = (value) => (Number.isFinite(value) ? formatPercent(value) : "n/a");
  return (
    <section className="accessibility-card" aria-labelledby="accessibility-title">
      <div>
        <span className="eyebrow">Wheels &amp; strollers</span>
        <h3 id="accessibility-title">Accessibility check</h3>
        <p>Steps, wheelchair tags, and surface types from OpenStreetMap.</p>
      </div>
      <div className="mural-controls">
        <button type="button" className="button button--secondary" onClick={check} disabled={busy || !points.length}>
          {busy ? "Checking..." : report ? "Re-check" : "Check accessibility"}
        </button>
      </div>
      {report && report.available && (
        <>
          <dl className="readiness-metrics">
            <div>
              <dt>Wheelchair: yes</dt>
              <dd>{share(report.wheelchair_yes_share)}</dd>
            </div>
            <div>
              <dt>Restricted / steps</dt>
              <dd>{share((report.wheelchair_no_share ?? 0) + (report.steps_share ?? 0))}</dd>
            </div>
            <div>
              <dt>Untagged</dt>
              <dd>{share(report.untagged_share)}</dd>
            </div>
          </dl>
          {report.message && <p className="mural-note">{report.message}</p>}
          {(report.concerns ?? []).length > 0 && (
            <button type="button" className="button button--secondary" aria-pressed={active} onClick={toggleOverlay}>
              {active ? "Hide barrier sections" : "Show barriers on map"}
            </button>
          )}
        </>
      )}
      {report && !report.available && <p className="mural-note">{report.message}</p>}
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>Untagged streets are unknown, not accessible. Survey critical sections in person.</small>
    </section>
  );
}

function buildWorksheetSvg(points) {
  if (!Array.isArray(points) || points.length < 3) return null;
  const latitudes = points.map((point) => point[0]);
  const longitudes = points.map((point) => point[1]);
  const minLat = Math.min(...latitudes);
  const maxLat = Math.max(...latitudes);
  const minLon = Math.min(...longitudes);
  const maxLon = Math.max(...longitudes);
  const spanLat = Math.max(maxLat - minLat, 1e-9);
  const spanLon = Math.max(maxLon - minLon, 1e-9);
  const size = 200;
  const pad = 12;
  const scale = (size - pad * 2) / Math.max(spanLat, spanLon);
  const projectY = (lat) => pad + (maxLat - lat) * scale;
  const projectX = (lon) => pad + (lon - minLon) * scale;
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${projectX(point[1]).toFixed(1)} ${projectY(point[0]).toFixed(1)}`)
    .join(" ");
  return { path, size };
}

function LessonSheetOverlay({ open, pack, points, dedication, onDedicationChange, onClose }) {
  const dialogRef = useModalDialog(open && Boolean(pack), onClose, "lesson-print-open");
  if (!open || !pack) return null;
  const svg = buildWorksheetSvg(points);
  return (
    <div
      className="lesson-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Classroom worksheet preview"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="lesson-sheet">
        <label className="poster-dedication">
          Class / title
          <input
            data-dialog-initial-focus
            type="text"
            value={dedication}
            maxLength={80}
            placeholder="7b - The heart walk"
            onChange={(event) => onDedicationChange(event.target.value)}
          />
        </label>
        <header className="lesson-header">
          <h3>{pack.title}</h3>
          {dedication && <p className="lesson-subtitle">{dedication}</p>}
        </header>
        <div className="lesson-grid">
          <div className="lesson-drawing">
            {svg && (
              <svg viewBox={`0 0 ${svg.size} ${svg.size}`} role="img" aria-label="The drawing on graph paper">
                <defs>
                  <pattern id="lesson-grid" width="10" height="10" patternUnits="userSpaceOnUse">
                    <path d="M 10 0 L 0 0 0 10" fill="none" stroke="#cfd8d4" strokeWidth="0.5" />
                  </pattern>
                </defs>
                <rect width={svg.size} height={svg.size} fill="url(#lesson-grid)" />
                <path
                  d={svg.path}
                  fill="none"
                  stroke="#0b6b57"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
            <small>
              Sketch at scale 1 : {pack.scale_ratio} (1 cm on paper ≈ {(pack.scale_ratio / 100).toFixed(0)} m)
            </small>
          </div>
          <table className="lesson-table">
            <thead>
              <tr>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col">Bearing</th>
                <th scope="col">Compass</th>
                <th scope="col">Distance</th>
              </tr>
            </thead>
            <tbody>
              {pack.waypoints.map((waypoint) => (
                <tr key={waypoint.id}>
                  <td>{waypoint.id}</td>
                  <td>{waypoint.to_id}</td>
                  <td>{waypoint.bearing_deg}°</td>
                  <td>{waypoint.compass}</td>
                  <td>{waypoint.leg_distance_m} m</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="lesson-notes">
          {pack.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
        <p className="poster-footer">
          Total ≈ {pack.total_distance_km} km · {pack.waypoint_count} waypoints ·
          Planned with Paceasso · Map data © OpenStreetMap contributors
        </p>
        <div className="poster-actions">
          <button type="button" className="button button--primary" onClick={() => window.print()}>
            Print worksheet
          </button>
          <button type="button" className="button button--secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

const REEL_DURATIONS = [
  { value: 4, label: "4 s" },
  { value: 8, label: "8 s" },
  { value: 12, label: "12 s" },
];

function ReelOverlay({ open, onClose, startCapture, shapeName, city }) {
  const [duration, setDuration] = useState(8);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [videoUrl, setVideoUrl] = useState(null);
  const [fileExtension, setFileExtension] = useState("webm");
  const [captureReady, setCaptureReady] = useState(false);
  const previewRef = useRef(null);
  const captureRef = useRef(null);
  const dialogRef = useModalDialog(open, onClose);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setStatus("");
    setError("");
    setVideoUrl(null);
    setBusy(false);
    setCaptureReady(false);
    captureRef.current = null;
    Promise.resolve()
      .then(startCapture)
      .then((capture) => {
        if (!capture?.canvas) throw new Error("The map isn’t ready to record yet.");
        if (!cancelled) {
          captureRef.current = capture;
          setCaptureReady(true);
        }
      })
      .catch((captureError) => {
        if (!cancelled) setError(captureError.message || "We couldn’t prepare the map for recording.");
      });
    return () => {
      cancelled = true;
      captureRef.current = null;
      setCaptureReady(false);
    };
  }, [open, startCapture]);

  useEffect(() => () => {
    if (videoUrl) URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  if (!open) return null;

  const pickMimeType = () => {
    const candidates = [
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
      "video/mp4",
    ];
    for (const candidate of candidates) {
      if (window.MediaRecorder?.isTypeSupported?.(candidate)) return candidate;
    }
    return null;
  };

  const record = async () => {
    const capture = captureRef.current;
    if (!capture) return;
    const mimeType = pickMimeType();
    if (!mimeType || typeof capture.canvas.captureStream !== "function") {
      setError("This browser can’t record video. Try Chrome, Edge, or Firefox.");
      return;
    }
    setBusy(true);
    setError("");
    setStatus("Recording the drawing…");
    setVideoUrl(null);
    let stream = null;
    try {
      stream = capture.canvas.captureStream(30);
      const recorder = new MediaRecorder(stream, { mimeType });
      const chunks = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };
      const finished = new Promise((resolve) => {
        recorder.onstop = resolve;
      });
      recorder.start();
      const frames = Math.round(duration * 30);
      const holdFrames = Math.round(0.9 * 30);
      for (let frame = 0; frame <= frames + holdFrames; frame += 1) {
        const progress = Math.min(frame / frames, 1);
        capture.frame(progress);
        const preview = previewRef.current;
        if (preview && preview.tagName === "CANVAS") {
          const context = preview.getContext("2d");
          context.clearRect(0, 0, preview.width, preview.height);
          context.drawImage(capture.canvas, 0, 0);
        }
        await new Promise((resolve) => window.setTimeout(resolve, 1000 / 30));
      }
      setStatus("Finishing the file…");
      recorder.stop();
      await finished;
      const blob = new Blob(chunks, { type: mimeType.split(";")[0] });
      setFileExtension(mimeType.startsWith("video/mp4") ? "mp4" : "webm");
      setVideoUrl(URL.createObjectURL(blob));
      setStatus("Ready. Download it or record again.");
    } catch (recordError) {
      setError(recordError.message || "Recording failed. Try a shorter reel.");
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
      setBusy(false);
    }
  };

  const download = () => {
    if (!videoUrl) return;
    const anchor = document.createElement("a");
    anchor.href = videoUrl;
    anchor.download = `${safeFilePart(`${shapeName}-${city}`)}-reel.${fileExtension}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  return (
    <div
      className="reel-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Reel recorder"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="reel-sheet">
        <div className="reel-heading">
          <div>
            <span className="eyebrow">Share it moving</span>
            <h3>Record a drawing reel</h3>
          </div>
          <button type="button" className="reel-close" aria-label="Close reel recorder" onClick={onClose}>
            ×
          </button>
        </div>
        <p className="reel-intro">
          The route draws itself over the street map, ready for your feed. Everything is rendered
          in this browser tab; nothing uploads.
        </p>
        <div className="reel-controls">
          <label>
            Length
            <select
              data-dialog-initial-focus
              value={duration}
              onChange={(event) => setDuration(Number(event.target.value))}
              disabled={busy}
            >
              {REEL_DURATIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="button button--primary" onClick={record} disabled={busy || !captureReady}>
            {busy ? "Recording…" : !captureReady ? "Preparing…" : videoUrl ? "Record again" : "Record reel"}
          </button>
          {videoUrl && (
            <button type="button" className="button button--secondary" onClick={download}>
              Download {fileExtension.toUpperCase()}
            </button>
          )}
        </div>
        <canvas
          ref={previewRef}
          className="reel-preview"
          width={captureRef.current?.width ?? 640}
          height={captureRef.current?.height ?? 400}
          aria-label="Reel preview"
        />
        {status && <p className="reel-status" role="status">{status}</p>}
        {error && <p className="editor-error" role="alert">{error}</p>}
        <small>Recording happens locally at 30 fps. Reels include the OpenStreetMap attribution.</small>
      </div>
    </div>
  );
}

const CAMPAIGN_SHAPE_IDEAS = [
  "ribbon",
  "heart",
  "star",
  "circle",
  "cross",
  "lightning",
];

function buildCampaignLink({ name, shape, hashtag, until }) {
  const params = new URLSearchParams();
  params.set("campaign", name);
  params.set("shape", shape);
  params.set("mode", "group");
  if (hashtag) params.set("hashtag", hashtag.startsWith("#") ? hashtag : `#${hashtag}`);
  if (until) params.set("until", until);
  return `${window.location.origin}${window.location.pathname}?${params.toString()}`;
}

function validateCampaignName(value) {
  const name = value.trim();
  if (!name) return "Enter a campaign name.";
  if ([...name].length < 2) return "Campaign name must be at least 2 characters.";
  if ([...name].length > 60) return "Campaign name must be 60 characters or fewer.";
  if (!/^[\p{L}\p{N}][\p{L}\p{M}\p{N} .,'’&()/-]*$/u.test(name)) {
    return "Use letters, numbers, spaces, or common punctuation in the campaign name.";
  }
  return "";
}

function campaignSlug(name) {
  const slug = name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "")
    .slice(0, 41);
  return slug.length >= 2 ? slug : "art-campaign";
}

function localIsoDate(date = new Date()) {
  const localTime = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localTime.toISOString().slice(0, 10);
}

function parseCampaignFromUrl() {
  try {
    const params = new URLSearchParams(window.location.search);
    const name = (params.get("campaign") || "").trim();
    if (validateCampaignName(name)) return null;
    const shape = (params.get("shape") || "").trim();
    if (!/^[a-z_]{2,40}$/.test(shape)) return null;
    const hashtag = (params.get("hashtag") || "").trim().slice(0, 60);
    const untilRaw = params.get("until") || "";
    const until = /^\d{4}-\d{2}-\d{2}$/.test(untilRaw) ? untilRaw : null;
    return {
      name,
      slug: campaignSlug(name),
      shape,
      hashtag,
      until,
    };
  } catch {
    return null;
  }
}

function CampaignBuilderOverlay({ open, onClose }) {
  const [name, setName] = useState("");
  const [shape, setShape] = useState("ribbon");
  const [hashtag, setHashtag] = useState("");
  const [until, setUntil] = useState("");
  const [link, setLink] = useState(null);
  const [copied, setCopied] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [copyError, setCopyError] = useState("");
  const dialogRef = useModalDialog(open, onClose);
  useEffect(() => {
    if (!open) {
      setLink(null);
      setCopied(false);
      setAttempted(false);
      setCopyError("");
    }
  }, [open]);
  useEffect(() => {
    setLink(null);
    setCopied(false);
    setCopyError("");
  }, [hashtag, name, shape, until]);
  if (!open) return null;
  const nameError = validateCampaignName(name);
  const dateError = until && until < localIsoDate() ? "Choose today or a future end date." : "";
  const create = (event) => {
    event.preventDefault();
    setAttempted(true);
    if (nameError || dateError || !shape) return;
    setLink(buildCampaignLink({ name: name.trim(), shape, hashtag: hashtag.trim(), until }));
    setCopied(false);
    setCopyError("");
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setCopyError("");
    } catch {
      setCopied(false);
      setCopyError("The link could not be copied. Select the link above and copy it manually.");
    }
  };
  return (
    <div
      className="campaign-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Charity art campaign builder"
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="campaign-sheet">
        <div className="reel-heading">
          <div>
            <span className="eyebrow">Organise</span>
            <h3>Charity art campaign kit</h3>
          </div>
          <button type="button" className="reel-close" aria-label="Close campaign builder" onClick={onClose}>
            ×
          </button>
        </div>
        <p className="reel-intro">
          Give your supporters one link. It opens this planner with the campaign drawing already
          filled in, and every published map joins the campaign gallery. No accounts, no tracking.
        </p>
        <form className="campaign-form" onSubmit={create} noValidate>
          <label>
            Campaign name
            <input
              data-dialog-initial-focus
              type="text"
              value={name}
              maxLength={60}
              placeholder="Pink Ribbon Budapest 2026"
              onChange={(event) => setName(event.target.value)}
              aria-invalid={attempted && Boolean(nameError)}
              aria-errormessage={attempted && nameError ? "campaign-name-error" : undefined}
              required
            />
            {attempted && nameError && (
              <span id="campaign-name-error" className="field-error" role="alert">
                <span aria-hidden="true">!</span>
                {nameError}
              </span>
            )}
          </label>
          <label>
            Drawing
            <select value={shape} onChange={(event) => setShape(event.target.value)}>
              {CAMPAIGN_SHAPE_IDEAS.map((idea) => (
                <option key={idea} value={idea}>
                  {normaliseLabel(idea)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Hashtag (optional)
            <input
              type="text"
              value={hashtag}
              maxLength={60}
              placeholder="#runforcare"
              onChange={(event) => setHashtag(event.target.value)}
            />
          </label>
          <label>
            Runs until (optional)
            <input
              type="date"
              value={until}
              min={localIsoDate()}
              onChange={(event) => setUntil(event.target.value)}
              aria-invalid={attempted && Boolean(dateError)}
              aria-errormessage={attempted && dateError ? "campaign-date-error" : undefined}
            />
            {attempted && dateError && (
              <span id="campaign-date-error" className="field-error" role="alert">
                <span aria-hidden="true">!</span>
                {dateError}
              </span>
            )}
          </label>
          <div className="campaign-actions">
            <button type="submit" className="button button--primary">
              Create campaign link
            </button>
          </div>
        </form>
        {link && (
          <div className="campaign-link" role="status">
            <code>{link}</code>
            <div className="campaign-link-actions">
              <button type="button" className="button button--secondary" onClick={copy}>
                {copied ? "Copied!" : "Copy link"}
              </button>
              <a className="button button--secondary" href={link}>
                Open it
              </a>
            </div>
          </div>
        )}
        {copyError && <p className="editor-error" role="alert">{copyError}</p>}
        <small>The drawing list covers simple campaign shapes; supporters can still edit the prompt freely.</small>
      </div>
    </div>
  );
}

const GPS_ACCURACY_PROFILES = [
  { value: 5, label: "Open sky / dual-band (5 m)" },
  { value: 10, label: "Typical phone or watch (10 m)" },
  { value: 20, label: "Tall buildings or trees (20 m)" },
];

function InkproofCard({ points, overlayType, onOverlayChange }) {
  const [accuracy, setAccuracy] = useState("10");
  const [analysis, setAnalysis] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const showing = overlayType === "inkproof";

  const overlay = useCallback((result) => ({
    type: "inkproof",
    segments: (result?.fragile_segments ?? []).map((segment) => ({
      ...segment,
      kind: "inkproof",
    })),
  }), []);

  const check = async () => {
    if (!Array.isArray(points) || points.length < 4 || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await analyseInkproof({
        points,
        accuracy_m: Number(accuracy),
      });
      setAnalysis(response);
      onOverlayChange(response.fragile_segments?.length ? overlay(response) : null);
    } catch (requestError) {
      setError(requestError.message || "We couldn’t test this drawing against GPS drift.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="inkproof-card" aria-labelledby="inkproof-title">
      <div>
        <span className="eyebrow">Free · before you go</span>
        <h3 id="inkproof-title">GPS detail check</h3>
        <p>Simulate GPS drift and find details that may disappear from the recorded artwork.</p>
      </div>
      <div className="niche-controls">
        <label>
          Expected accuracy
          <select value={accuracy} onChange={(event) => setAccuracy(event.target.value)}>
            {GPS_ACCURACY_PROFILES.map((profile) => (
              <option key={profile.value} value={profile.value}>{profile.label}</option>
            ))}
          </select>
        </label>
        <button type="button" className="button button--secondary" onClick={check} disabled={busy}>
          {busy ? "Simulating..." : "Test recording durability"}
        </button>
      </div>
      {analysis && (
        <div className="niche-result" role="status">
          <div className="niche-score-row">
            <strong>{formatPercent(analysis.resilience_score)} inkproof</strong>
            <span>{normaliseLabel(analysis.rating)}</span>
          </div>
          <p>
            Expected recognition {formatPercent(analysis.expected_recognition)} · {formatPercent(analysis.fragile_share)} of the line needs extra care.
          </p>
          {analysis.fragile_segments?.length > 0 ? (
            <button
              type="button"
              className="niche-map-toggle"
              onClick={() => onOverlayChange(showing ? null : overlay(analysis))}
            >
              {showing ? "Hide fragile ink on map" : `Show ${analysis.fragile_segments.length} fragile area${analysis.fragile_segments.length === 1 ? "" : "s"} on map`}
            </button>
          ) : (
            <span className="niche-success">No structurally fragile details found.</span>
          )}
          <ul className="niche-tips">
            {(analysis.tips ?? []).slice(0, 2).map((tip) => <li key={tip}>{tip}</li>)}
          </ul>
        </div>
      )}
      {error && <p className="editor-error" role="alert">{error}</p>}
      <small>No paid map call: the test runs from route geometry only.</small>
    </section>
  );
}

function loadingStageIndex(stage) {
  if (stage?.startsWith("polish.") || ["route_review", "export"].includes(stage)) return 3;
  if (["snap", "validation", "refinement"].includes(stage)) return 2;
  if (["placement", "preflight"].includes(stage)) return 1;
  return 0;
}

const POLISH_STAGE_NAMES = {
  "polish.shape": "the outline",
  "polish.reversals": "unnecessary backtracking",
  "polish.detours": "street detours",
  "polish.contour": "the route contour",
  "polish.reference_contour": "recognisable details",
  "polish.turns": "distinctive turns",
  "polish.graph": "the street graph",
  "polish.post_graph_contour": "a newly exposed detour",
  "polish.post_graph_reference": "the final outline details",
  "polish.distance": "the requested distance",
};

function loadingStageMessage(progress) {
  if (progress?.polishPhase) {
    const subject = POLISH_STAGE_NAMES[progress.polishPhase] ?? "a final street alternative";
    return `Final polish: checking ${subject}.`;
  }
  const stage = progress?.stage;
  return {
    intent: "Reading your idea and its route constraints.",
    planning: "Planning a drawing that could fit the city.",
    shape: "Preparing the outline and its recognisable details.",
    placement: "Placing the drawing near real streets.",
    preflight: "Screening placements before full street routing.",
    snap: "Asking the routing provider for a connected street route.",
    validation: "Measuring likeness, distance, and closure.",
    refinement: "Trying another measured placement.",
    route_review: "Reviewing the strongest street-routed candidates.",
    export: "Preparing the final checked route.",
  }[stage] ?? "Working through the route checks.";
}

const ROUTE_LOADING_STAGES = [
  ["Outline", "Reading the idea and shaping the line."],
  ["Street fit", "Trying useful positions on the road network."],
  ["Route checks", "Comparing likeness, distance, and closure."],
  ["Final polish", "Keeping the strongest measured route."],
];

const IMAGE_LOADING_STAGES = [
  ["Image read", "Finding the silhouette and its key details."],
  ["GPS sketch", "Drawing route-friendly alternatives."],
  ["Street fit", "Matching the best outline to real roads."],
  ["Final check", "Protecting recognisability and distance."],
];

const LOADING_FACTS = [
  "GPS art is a compromise between a clean silhouette and streets that actually connect.",
  "Corners and distinctive turns matter more for recognition than lots of tiny detail.",
  "The first plausible route is not automatically the winner; alternatives are measured too.",
  "A route can look good as a sketch and still need a different rotation to fit the city.",
  "A convincing heart needs both lobes; a convincing route needs connected streets.",
  "An elegant shortcut on a drawing may be a wall, river, or railway on the ground.",
  "The map gets a vote. It is surprisingly opinionated about sharp corners.",
  "Rotating a drawing a few degrees can turn a dead end into a usable street sequence.",
  "Distance and recognisability are checked separately; neither gets a free pass.",
  "A route preview is not a GPX certificate. The final checks still matter.",
];

function LoadingState({ onCancel, kind = "route", focusRef, progress, preview }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 48rem)").matches);
  const dialogRef = useModalDialog(compact, onCancel);
  const setLoadingRef = useCallback((node) => {
    dialogRef.current = node;
    if (focusRef) focusRef.current = node;
  }, [dialogRef, focusRef]);
  const stages = kind === "image" ? IMAGE_LOADING_STAGES : ROUTE_LOADING_STAGES;
  const currentStage = progress?.stageIndex ?? -1;
  const factIndex = Math.floor(elapsedSeconds / 9) % LOADING_FACTS.length;
  const polishSeconds = progress?.polishPhaseStartedAt
    ? Math.max(0, Math.floor((Date.now() - progress.polishPhaseStartedAt) / 1000))
    : 0;
  const updateAge = progress?.receivedAt
    ? Math.max(0, Math.floor((Date.now() - progress.receivedAt) / 1000))
    : elapsedSeconds;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 48rem)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const startedAt = Date.now();
    const timer = window.setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1_000)),
      1_000,
    );
    return () => window.clearInterval(timer);
  }, []);

  return (
    <section
      ref={setLoadingRef}
      className="loading-card loading-card--journey"
      id="route-generation"
      role={compact ? "dialog" : undefined}
      aria-modal={compact ? true : undefined}
      aria-labelledby="loading-title"
      aria-describedby="loading-message loading-expectation"
      tabIndex="-1"
    >
      {preview ? (
        <div className="loading-preview">
          <div className="loading-preview-heading">
            <strong>First street-connected candidate</strong>
            <span>{preview.distance_km} km · still being checked</span>
          </div>
          <Suspense fallback={<p>Loading the early street map…</p>}>
            <RouteMap
              points={preview.points}
              idealPoints={[]}
              shapeName={preview.shape_name ?? "Route"}
              roadRouted
              accepted={false}
            />
          </Suspense>
          <small>This is an early ORS-routed candidate, not the final route. No GPX is available until the checks finish.</small>
        </div>
      ) : <div className="loading-visual" aria-hidden="true">
        <svg className="gps-route-animation" viewBox="0 0 360 190">
          <defs>
            <pattern id="loading-map-grid" width="28" height="28" patternUnits="userSpaceOnUse">
              <path d="M 28 0 L 0 0 0 28" />
            </pattern>
          </defs>
          <rect className="gps-route-grid" width="360" height="190" />
          <path className="gps-route-road gps-route-road--one" d="M-10 43 C72 60 108 24 177 42 S282 77 370 46" />
          <path className="gps-route-road gps-route-road--two" d="M36 -8 C49 57 15 91 43 198" />
          <path className="gps-route-road gps-route-road--three" d="M275 -8 C250 50 293 97 268 198" />
          <path
            className="gps-route-shadow"
            d="M26 145 C55 126 65 158 87 137 C105 120 116 70 151 66 C184 61 196 102 176 124 C153 148 118 126 122 95 C128 52 205 46 225 94 C241 132 266 153 331 111"
          />
          <path
            className="gps-route-line"
            d="M26 145 C55 126 65 158 87 137 C105 120 116 70 151 66 C184 61 196 102 176 124 C153 148 118 126 122 95 C128 52 205 46 225 94 C241 132 266 153 331 111"
          />
          <circle className="gps-route-start" cx="26" cy="145" r="8" />
          <circle className="gps-route-start-core" cx="26" cy="145" r="3" />
          <g className="gps-route-finish" transform="translate(331 111)">
            <path d="M0 0 V-27" />
            <path d="M2 -25 H20 L15 -16 H2 Z" />
          </g>
          <circle className="gps-route-traveller-halo" r="10" />
          <circle className="gps-route-traveller" r="5" />
        </svg>
        <div className="loading-visual-caption">
          <span>{kind === "image" ? "Pixels → path → streets" : "Sketch → streets → route"}</span>
          <b>Quality checks stay on</b>
        </div>
      </div>}

      <div className="loading-content">
        <div className="loading-heading-row">
          <div>
            <h1 id="loading-title" tabIndex={-1} data-dialog-initial-focus>
              {kind === "image" ? "Drawing from your image" : "Finding routes"}
            </h1>
          </div>
          <span aria-label={`${elapsedSeconds} seconds elapsed`}>{elapsedSeconds}s</span>
        </div>
        <div
          className="loading-progress-track"
          role="progressbar"
          aria-label="Route generation is in progress"
          aria-valuetext={progress ? loadingStageMessage(progress) : "Waiting for the route planner"}
        >
          <span />
        </div>
        <p id="loading-message" className="loading-message" role="status" aria-live="polite">
          {progress ? loadingStageMessage(progress) : "Connecting to the route planner. The streets are warming up."}
        </p>
        <p className="loading-expectation loading-llm" role="status" aria-label="LLM usage" aria-live="polite">
          {progress?.llm_call
            ? <>Latest LLM call: <strong>{progress.llm_call.provider}</strong> · {progress.llm_call.model}</>
            : "No LLM calls reported yet."}
        </p>
        <p id="loading-expectation" className="loading-expectation">
          {kind === "image"
            ? "We’ll show a street-connected preview as soon as the image outline has been routed."
            : "We compare street fits and show the first connected route while the remaining checks run."}
        </p>
        {updateAge >= 12 && (
          <p className="loading-quiet-note" role="status">
            The planner hasn’t sent a new update yet. Your request is still active; you can cancel below.
          </p>
        )}

        <div className="loading-stages-heading">
          <strong>Planning flow</strong>
          <span>{progress ? "Live from the route planner" : "Waiting for the first update"}</span>
        </div>
        <ol className="loading-stages" aria-label="Route planning stages">
          {stages.map(([title, detail], index) => {
            const status = index < currentStage
              ? "complete"
              : index === currentStage
                ? "current"
                : "upcoming";
            return (
              <li
                key={title}
                className={`loading-stage loading-stage--${status}`}
                aria-current={status === "current" ? "step" : undefined}
              >
                <span aria-hidden="true">{status === "complete" ? "✓" : index + 1}</span>
                <div>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </div>
              </li>
            );
          })}
        </ol>

        {progress?.polishStarted && (
          <div className="loading-polish-detail" role="status" aria-live="polite">
            <strong>Final polish · {progress.polishCompleted.length} checks completed</strong>
            <span>
              {progress.polishPhase
                ? `Checking ${POLISH_STAGE_NAMES[progress.polishPhase] ?? "a street alternative"} · ${polishSeconds}s on this check · ${progress.phase_routing_requests?.directions ?? 0} full route requests in this check`
                : "Polish checks finished; reviewing the strongest street route."}
            </span>
          </div>
        )}

        {progress && (
          <p className="loading-measured-work" role="status">
            {progress.preflight_count > 0 && <span>{progress.preflight_count} placements screened</span>}
            {progress.search_areas > 0 && <span>{progress.search_areas} search areas explored</span>}
            {progress.connected_candidates > 0 && <span>{progress.connected_candidates} street-connected candidates found</span>}
            <span>{progress.routing_requests?.directions ?? 0} full street route requests so far</span>
          </p>
        )}

        <WaitingSketchGame />
        <WaitingRunnerGame />

        <p className="loading-fact">
          <span>While you wait</span>
          {LOADING_FACTS[factIndex]}
        </p>
      </div>

      <button
        type="button"
        className="button button--quiet loading-cancel"
        onClick={onCancel}
      >
        Cancel
      </button>
    </section>
  );
}

function GalleryLightbox({ assets, activeIndex, onClose, onMove }) {
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const previousFocusRef = useRef(null);
  const asset = assets[activeIndex];

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const restoreBackground = inertDialogBackground(dialogRef.current);
    closeButtonRef.current?.focus();

    return () => {
      document.body.style.overflow = previousOverflow;
      restoreBackground();
      if (previousFocusRef.current?.isConnected) previousFocusRef.current.focus();
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        onMove(-1);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        onMove(1);
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = [...(dialogRef.current?.querySelectorAll(
        'button:not(:disabled), a[href]',
      ) ?? [])];
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!dialogRef.current?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, onMove]);

  if (!asset) return null;
  const hasMultipleAssets = assets.length > 1;

  return (
    <div
      className="gallery-lightbox-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="gallery-lightbox"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gallery-lightbox-title"
        aria-describedby="gallery-lightbox-position"
        ref={dialogRef}
      >
        <div className="gallery-lightbox-header">
          <div>
            <h2 id="gallery-lightbox-title">Gallery viewer</h2>
            <p id="gallery-lightbox-position" aria-live="polite">
              Image {activeIndex + 1} of {assets.length}
            </p>
          </div>
          <button
            type="button"
            className="gallery-lightbox-close"
            onClick={onClose}
            ref={closeButtonRef}
            aria-label="Close gallery viewer"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div className="gallery-lightbox-stage">
          {hasMultipleAssets && (
            <button
              type="button"
              className="gallery-lightbox-nav gallery-lightbox-nav--previous"
              onClick={() => onMove(-1)}
              aria-label="Previous gallery image"
            >
              <span aria-hidden="true">←</span>
            </button>
          )}
          <div className="gallery-lightbox-media">
            <img
              src={asset.preview_url || asset.image_url}
              alt={`Anonymous GPS art route, gallery image ${activeIndex + 1} of ${assets.length}`}
              width={asset.width || undefined}
              height={asset.height || undefined}
              decoding="async"
            />
          </div>
          {hasMultipleAssets && (
            <button
              type="button"
              className="gallery-lightbox-nav gallery-lightbox-nav--next"
              onClick={() => onMove(1)}
              aria-label="Next gallery image"
            >
              <span aria-hidden="true">→</span>
            </button>
          )}
        </div>

        <div className="gallery-lightbox-footer">
          <span>Use ← and → to browse. Press Esc to close.</span>
          <a href={asset.image_url} target="_blank" rel="noreferrer">
            Open original
          </a>
        </div>
      </div>
    </div>
  );
}

function GallerySection({ refreshKey = 0, publishedAsset = null, campaignSlug = null, onOpenCampaignBuilder }) {
  const [assets, setAssets] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [configured, setConfigured] = useState(true);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [removalTokens, setRemovalTokens] = useState(readGalleryRemovalTokens);
  const [activeAssetId, setActiveAssetId] = useState(null);
  const [campaignFilter, setCampaignFilter] = useState(campaignSlug);
  const removedAssetIdsRef = useRef(new Set());
  const galleryRequestRef = useRef({ sequence: 0, controller: null });

  useEffect(() => {
    setCampaignFilter(campaignSlug);
  }, [campaignSlug]);

  const activeAssetIndex = assets.findIndex((asset) => asset.id === activeAssetId);

  const moveLightbox = useCallback((offset) => {
    setActiveAssetId((currentId) => {
      if (assets.length === 0) return null;
      const currentIndex = assets.findIndex((asset) => asset.id === currentId);
      const safeIndex = currentIndex >= 0 ? currentIndex : 0;
      const nextIndex = (safeIndex + offset + assets.length) % assets.length;
      return assets[nextIndex].id;
    });
  }, [assets]);

  const loadGalleryPage = useCallback(async (cursor = null, replace = false) => {
    const requestId = galleryRequestRef.current.sequence + 1;
    galleryRequestRef.current.sequence = requestId;
    galleryRequestRef.current.controller?.abort();
    const controller = new AbortController();
    galleryRequestRef.current.controller = controller;
    if (replace) setLoading(true);
    else setLoadingMore(true);
    setError("");
    try {
      const response = await listGallery({
        cursor,
        limit: 12,
        campaign: campaignFilter,
        signal: controller.signal,
      });
      if (galleryRequestRef.current.sequence !== requestId) return;
      setConfigured(response.configured !== false);
      setAssets((current) =>
        mergeGalleryAssets(current, response.assets, {
          replace,
          publishedAsset,
          removedIds: removedAssetIdsRef.current,
        }),
      );
      setNextCursor(response.next_cursor ?? null);
    } catch (galleryError) {
      if (galleryError?.name === "AbortError") return;
      if (galleryRequestRef.current.sequence !== requestId) return;
      setError(galleryError.message || "We couldn’t load the gallery. Please try again.");
    } finally {
      if (galleryRequestRef.current.sequence === requestId) {
        galleryRequestRef.current.controller = null;
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [publishedAsset, campaignFilter]);

  useEffect(() => () => {
    galleryRequestRef.current.sequence += 1;
    galleryRequestRef.current.controller?.abort();
  }, []);

  useEffect(() => {
    setRemovalTokens(readGalleryRemovalTokens());
    loadGalleryPage(null, true);
  }, [loadGalleryPage, refreshKey]);

  const removeAsset = useCallback(async (asset) => {
    const token = removalTokens[asset.id];
    if (!token || !window.confirm("Remove your map from the public gallery?")) return;
    setError("");
    try {
      await removeGalleryImage({ public_id: asset.id, removal_token: token });
      removedAssetIdsRef.current.add(asset.id);
      setAssets((current) => current.filter((item) => item.id !== asset.id));
      setActiveAssetId((currentId) => (currentId === asset.id ? null : currentId));
      setRemovalTokens(forgetGalleryRemovalToken(asset.id));
    } catch (removalError) {
      setError(removalError.message || "We couldn’t remove that map. Please try again.");
    }
  }, [removalTokens]);

  return (
    <section className="gallery" id="gallery" aria-labelledby="gallery-title">
      <div className="section-heading gallery-heading">
        <div>
          <h1 id="gallery-title">Public gallery</h1>
          <p>Map images shared by users. Prompts, profiles, and route files are not published.</p>
        </div>
        <div className="gallery-heading-actions">
          {campaignFilter && (
            <button
              type="button"
              className="button button--secondary campaign-filter-chip"
              onClick={() => setCampaignFilter(null)}
              aria-pressed="true"
            >
              {normaliseLabel(campaignFilter)} ×
            </button>
          )}
          <button
            type="button"
            className="button button--quiet"
            onClick={(event) => {
              // Safari/WebKit does not focus a button after a pointer click.
              // Focus it explicitly so modal cleanup has a reliable return target.
              event.currentTarget.focus({ preventScroll: true });
              onOpenCampaignBuilder?.();
            }}
          >
            Organise a campaign
          </button>
          <a className="button button--quiet" href="#route-designer">
            Plan a route
          </a>
        </div>
      </div>

      {loading && (
        <div className="gallery-state" role="status">
          Loading public map screenshots…
        </div>
      )}
      {!loading && !configured && (
        <div className="gallery-state">
          <strong>Gallery unavailable</strong>
          <span>Route planning and downloads still work.</span>
        </div>
      )}
      {error && (
        <p className="gallery-error" role="alert">
          {error}
        </p>
      )}
      {!loading && configured && assets.length === 0 && !error && (
        <div className="gallery-state gallery-state--empty">
          <img className="gallery-empty-mark" src="/favicon.svg" alt="" width="56" height="56" />
          <strong>No maps have been shared.</strong>
          <span>Publish a route map to add the first.</span>
        </div>
      )}
      {assets.length > 0 && (
        <div className="gallery-grid">
          {assets.map((asset, index) => (
            <article className="gallery-card" key={asset.id}>
              <button
                type="button"
                className="gallery-thumbnail"
                onClick={(event) => {
                  event.currentTarget.focus({ preventScroll: true });
                  setActiveAssetId(asset.id);
                }}
                aria-label={`Open gallery image ${index + 1} of ${assets.length}`}
              >
                <img
                  src={asset.thumbnail_url || asset.image_url}
                  alt="Anonymous GPS art route on an OpenStreetMap street map"
                  loading={index < 4 ? "eager" : "lazy"}
                  fetchPriority={index === 0 ? "high" : "low"}
                  decoding="async"
                  width={asset.width || undefined}
                  height={asset.height || undefined}
                />
              </button>
              {removalTokens[asset.id] && (
                <div>
                  <button type="button" onClick={() => removeAsset(asset)}>
                    Remove my post
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      {activeAssetIndex >= 0 && (
        <GalleryLightbox
          assets={assets}
          activeIndex={activeAssetIndex}
          onClose={() => setActiveAssetId(null)}
          onMove={moveLightbox}
        />
      )}
      {nextCursor && (
        <button
          type="button"
          className="button button--secondary gallery-more"
          onClick={() => loadGalleryPage(nextCursor, false)}
          disabled={loadingMore}
        >
          {loadingMore ? "Loading…" : "Show more routes"}
        </button>
      )}
      <p className="gallery-attribution">
        Map data ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap contributors
        </a>
        .
      </p>
    </section>
  );
}

function ResultPanel({ result, onDownload, onGalleryPublished, onEditRequest, focusRef, campaignSlug = null, active = true, planningMode = "personal", onOpenCampaignBuilder }) {
  const candidates = result.candidates ?? [];
  const [selectedCandidateId, setSelectedCandidateId] = useState(
    candidates[0]?.id ?? "best",
  );
  const [requestDetailsOpen, setRequestDetailsOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [controlPoints, setControlPoints] = useState([]);
  const [editedRoute, setEditedRoute] = useState(null);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState("");
  const [editDirty, setEditDirty] = useState(false);
  const [acceptedRouteIds, setAcceptedRouteIds] = useState(() => new Set());
  const [galleryConsent, setGalleryConsent] = useState(false);
  const [galleryBusy, setGalleryBusy] = useState(false);
  const [galleryError, setGalleryError] = useState("");
  const [publishedAsset, setPublishedAsset] = useState(null);
  const [repairBusy, setRepairBusy] = useState(false);
  const [repairNotice, setRepairNotice] = useState("");
  const [activeConcernCode, setActiveConcernCode] = useState(null);
  const [labOverlay, setLabOverlay] = useState(null);
  const [sightMarkers, setSightMarkers] = useState([]);
  const [posterOpen, setPosterOpen] = useState(false);
  const [posterImage, setPosterImage] = useState(null);
  const [posterError, setPosterError] = useState("");
  const [posterDedication, setPosterDedication] = useState("");
  const [reelOpen, setReelOpen] = useState(false);
  const [lessonOpen, setLessonOpen] = useState(false);
  const [lessonPack, setLessonPack] = useState(null);
  const [lessonPoints, setLessonPoints] = useState([]);
  const [lessonDedication, setLessonDedication] = useState("");
  const mapCaptureRef = useRef(null);
  const routeMutationRef = useRef(null);
  const cancelRouteMutation = useCallback(() => {
    routeMutationRef.current?.abort();
    routeMutationRef.current = null;
    setEditBusy(false);
    setRepairBusy(false);
  }, []);

  useEffect(() => {
    if (!active) {
      // Hidden dialogs must release their focus trap and document scroll lock.
      setPosterOpen(false);
      setReelOpen(false);
      setLessonOpen(false);
    }
  }, [active, posterOpen, reelOpen, lessonOpen]);

  const startReelCapture = useCallback(
    () => mapCaptureRef.current?.startReelCapture(),
    [],
  );
  const openLessonSheet = useCallback((pack, points) => {
    setLessonPack(pack);
    setLessonPoints(points);
    setLessonOpen(true);
  }, []);

  const updateLabOverlay = useCallback((value) => {
    setActiveConcernCode(null);
    setLabOverlay(value);
  }, []);

  useEffect(() => {
    cancelRouteMutation();
    setSelectedCandidateId(candidates[0]?.id ?? "best");
    setEditing(false);
    setControlPoints([]);
    setEditedRoute(null);
    setEditError("");
    setEditDirty(false);
    setAcceptedRouteIds(new Set());
    setGalleryConsent(false);
    setGalleryBusy(false);
    setGalleryError("");
    setPublishedAsset(null);
    setActiveConcernCode(null);
    setLabOverlay(null);
    setSightMarkers([]);
    setRequestDetailsOpen(false);
    setRepairNotice("");
    return () => {
      routeMutationRef.current?.abort();
      routeMutationRef.current = null;
    };
  }, [result.request_id, result.prompt, cancelRouteMutation]);

  const chooseCandidate = useCallback((candidateId) => {
    cancelRouteMutation();
    setSelectedCandidateId(candidateId);
    setEditing(false);
    setEditedRoute(null);
    setControlPoints([]);
    setEditError("");
    setEditDirty(false);
    setGalleryConsent(false);
    setGalleryError("");
    setPublishedAsset(null);
    setActiveConcernCode(null);
    setLabOverlay(null);
    setSightMarkers([]);
    setRepairNotice("");
  }, [cancelRouteMutation]);

  const selectedCandidate =
    candidates.find((candidate) => candidate.id === selectedCandidateId) ??
    candidates[0] ??
    null;
  const activeRoute =
    editedRoute ??
    selectedCandidate ?? {
      id: "best",
      shape_name: result.shape?.name,
      points_preview: result.points_preview,
      ideal_preview: result.ideal_preview,
      distance_km: result.distance_km,
      snapped: result.snapped,
      closed: result.shape?.closed,
      target_distance_km: result.intent?.distance_km,
      validation: result.validation,
      below_recommended: result.below_threshold,
      verification: result.route_verification,
      details: result.route_details,
      gpx: result.gpx,
      tcx: result.tcx,
      gallery_publish_token: result.gallery_publish_token,
    };
  const validation = activeRoute.validation ?? result.validation;
  const verification = activeRoute.verification ?? result.route_verification;
  const routeDetails = activeRoute.details ?? result.route_details;
  const distanceDetails = routeDetails?.distance ?? {};
  const routingDetails = routeDetails?.routing ?? {};
  const placementDetails = routeDetails?.placement ?? {};
  const deviationDetails = routeDetails?.deviation ?? {};
  const routeReadiness = routeDetails?.readiness ?? {};
  const readinessConcerns = Array.isArray(routeReadiness.concerns)
    ? routeReadiness.concerns
    : [];
  const mappedConcernCount = readinessConcerns.reduce(
    (count, concern) => count + (concern.segments_preview?.length ?? 0),
    0,
  );
  const score = validation?.score;
  const automaticChecksPassed = verification
    ? Boolean(verification.passed)
    : Boolean(activeRoute.snapped) && !Boolean(activeRoute.below_recommended);
  const activeRouteId = String(activeRoute.id ?? "best");
  const userAccepted = acceptedRouteIds.has(activeRouteId);
  const roadRouted = Boolean(activeRoute.snapped);
  const exportReady = roadRouted && (automaticChecksPassed || userAccepted);
  const exportBlockedByPendingEdits = editing && editDirty;
  const shapeName = normaliseLabel(activeRoute.shape_name ?? result.shape?.name);
  const shapeSource = activeRoute.shape_source ?? result.shape?.source;
  const aiDrawingReview = result.shape?.semantic_verification;
  const aiDrawingCueCount = aiDrawingReview?.cue_results?.filter(
    (cue) => cue.present,
  ).length;
  const fitDecision = result.fit_decision;
  const requestedShape = normaliseLabel(
    fitDecision?.requested_shape ?? result.requested_shape ?? result.shape?.name,
  );
  const understoodDrawing = normaliseLabel(
    result.intent?.text
      ? `Text “${result.intent.text}”`
      : result.requested_shape ?? result.intent?.shape ?? result.shape?.name,
  );
  const city = result.intent?.city ? normaliseLabel(result.intent.city) : "your selected area";
  const activity = activityLabel(result.intent?.sport);
  const targetDistance =
    activeRoute.target_distance_km ?? result.intent?.distance_km ?? distanceDetails.target_km;
  const planningOptions = result.planning_options ?? {};
  const enabledPreferences = Object.entries(planningOptions.route_preferences ?? {})
    .filter(([, enabled]) => enabled)
    .map(([key]) => normaliseLabel(key.replace(/^avoid /, "")));
  const candidateSummary = result.candidate_summary ?? {};
  const reviewCount = Number.isFinite(candidateSummary.review_count)
    ? candidateSummary.review_count
    : Number.isFinite(candidateSummary.rejected_selected_shape_count)
      ? candidateSummary.rejected_selected_shape_count
    : 0;
  const issueList = [
    ...new Set([
      ...(validation?.issues ?? []),
      ...(editedRoute?.warnings ?? []),
      ...(result.errors ?? []),
    ]),
  ];
  const stateLabel = automaticChecksPassed
    ? "Ready to download"
    : userAccepted
      ? "Approved by you"
      : activeRoute.snapped
        ? "Check before downloading"
        : "Map preview only";
  const canPublishGallery = Boolean(
    activeRoute.gallery_publish_token &&
    activeRoute.snapped &&
    exportReady &&
    !editing,
  );

  useEffect(() => {
    setGalleryConsent(false);
    setGalleryError("");
    setPublishedAsset(null);
  }, [activeRouteId]);

  const resetEditor = useCallback(() => {
    cancelRouteMutation();
    setControlPoints(sampleControlPoints(activeRoute.points_preview));
    setEditError("");
    setEditDirty(false);
  }, [activeRoute.points_preview, cancelRouteMutation]);

  const handleEditPoint = useCallback(
    (index, point) => {
      cancelRouteMutation();
      setEditDirty(true);
      setControlPoints((current) => {
        const next = current.map((item) => [...item]);
        next[index] = point;
        if (activeRoute.closed && next.length > 1) {
          if (index === 0) next[next.length - 1] = [...point];
          if (index === next.length - 1) next[0] = [...point];
        }
        return next;
      });
    },
    [activeRoute.closed, cancelRouteMutation],
  );

  const rerouteEdited = useCallback(async () => {
    if (controlPoints.length < 2 || editBusy || repairBusy) return;
    const controller = new AbortController();
    routeMutationRef.current = controller;
    setEditBusy(true);
    setEditError("");
    try {
      const response = await editRoute({
        control_points: controlPoints,
        reference_points:
          activeRoute.ideal_preview?.length > 1
            ? activeRoute.ideal_preview
            : controlPoints,
        sport: result.intent?.sport === "bike" ? "bike" : "run",
        closed: Boolean(activeRoute.closed),
        target_distance_km:
          activeRoute.target_distance_km ?? result.intent?.distance_km ?? null,
        name: `${shapeName} in ${city}`,
        shape_name: activeRoute.shape_name ?? result.shape?.name ?? "edited",
        route_preferences: result.planning_options?.route_preferences ?? undefined,
      }, { signal: controller.signal });
      if (routeMutationRef.current !== controller) return;
      const editedRouteId = `${selectedCandidate?.id ?? "best"}-edited`;
      setAcceptedRouteIds((current) => {
        const next = new Set(current);
        next.delete(editedRouteId);
        return next;
      });
      setEditedRoute({
        ...activeRoute,
        id: editedRouteId,
        points_preview: response.points_preview,
        distance_km: response.distance_km,
        snapped: response.snapped,
        validation: response.validation,
        verification: response.route_verification,
        details: response.route_details,
        below_recommended:
          response.below_recommended ??
          !response.route_verification?.passed,
        gpx: response.gpx,
        tcx: response.tcx,
        allow_gallery_share: false,
        warnings: response.warnings,
      });
      setControlPoints(sampleControlPoints(response.points_preview));
      setEditDirty(false);
    } catch (error) {
      if (routeMutationRef.current === controller && error.name !== "AbortError") {
        setEditError(error.message || "We couldn’t update that route. Please try again.");
      }
    } finally {
      if (routeMutationRef.current === controller) {
        routeMutationRef.current = null;
        setEditBusy(false);
      }
    }
  }, [
    activeRoute,
    city,
    controlPoints,
    editBusy,
    repairBusy,
    result.intent,
    result.planning_options,
    selectedCandidate?.id,
    shapeName,
  ]);

  const publishMapScreenshot = useCallback(async () => {
    if (!canPublishGallery || !galleryConsent || galleryBusy) return;
    setGalleryBusy(true);
    setGalleryError("");
    try {
      const imageDataUrl = await mapCaptureRef.current?.capturePng();
      if (!imageDataUrl) throw new Error("The map isn’t ready to share yet.");
      const response = await publishGalleryImage({
        image_data_url: imageDataUrl,
        publish_token: activeRoute.gallery_publish_token,
        confirm_public_location: true,
        campaign: campaignSlug,
      });
      rememberGalleryRemovalToken(response.asset.id, response.removal_token);
      setPublishedAsset(response.asset);
      onGalleryPublished?.(response.asset);
    } catch (publishError) {
      setGalleryError(
        publishError.message || "We couldn’t share the map. Please try again.",
      );
    } finally {
      setGalleryBusy(false);
    }
  }, [
    activeRoute.gallery_publish_token,
    campaignSlug,
    canPublishGallery,
    galleryBusy,
    galleryConsent,
    onGalleryPublished,
  ]);

  const openGiftPoster = useCallback(async () => {
    setPosterError("");
    try {
      const imageDataUrl = await mapCaptureRef.current?.capturePng();
      if (!imageDataUrl) throw new Error("The map isn’t ready to share yet.");
      setPosterImage(imageDataUrl);
      setPosterOpen(true);
    } catch (posterCaptureError) {
      setPosterError(posterCaptureError.message || "We couldn’t capture the map for the poster.");
    }
  }, []);

  const improveRecognition = useCallback(async () => {
    if (repairBusy || editBusy || !(activeRoute.ideal_preview ?? []).length) return;
    const controller = new AbortController();
    routeMutationRef.current = controller;
    setRepairBusy(true);
    setRepairNotice("");
    try {
      const response = await repairRecognition({
        reference_points: activeRoute.ideal_preview,
        sport: result.intent?.sport === "bike" ? "bike" : "run",
        closed: Boolean(activeRoute.closed),
        name: `${shapeName} refined in ${city}`,
        shape_name: activeRoute.shape_name ?? result.shape?.name ?? "edited",
        target_distance_km:
          activeRoute.target_distance_km ?? result.intent?.distance_km ?? null,
        route_preferences: result.planning_options?.route_preferences ?? undefined,
      }, { signal: controller.signal });
      if (routeMutationRef.current !== controller) return;
      const repairedRouteId = `${activeRouteId}-recognition-repair`;
      setAcceptedRouteIds((current) => {
        const next = new Set(current);
        next.delete(repairedRouteId);
        return next;
      });
      setEditedRoute({
        ...activeRoute,
        id: repairedRouteId,
        points_preview: response.points_preview,
        distance_km: response.distance_km,
        snapped: response.snapped,
        validation: response.validation,
        verification: response.route_verification,
        details: response.route_details,
        below_recommended: response.below_recommended ?? !response.route_verification?.passed,
        gpx: response.gpx,
        tcx: response.tcx,
        gallery_publish_token: response.gallery_publish_token ?? null,
        allow_gallery_share: true,
        warnings: response.warnings,
      });
      setEditing(false);
      setControlPoints([]);
      setEditDirty(false);
      setRepairNotice(`${response.message} Recognition score: ${formatPercent(response.recognition_score)}.`);
    } catch (repairError) {
      if (routeMutationRef.current === controller && repairError.name !== "AbortError") {
        setRepairNotice(repairError.message || "We couldn’t refine this drawing.");
      }
    } finally {
      if (routeMutationRef.current === controller) {
        routeMutationRef.current = null;
        setRepairBusy(false);
      }
    }
  }, [
    activeRoute,
    activeRouteId,
    city,
    editBusy,
    repairBusy,
    result.intent,
    result.planning_options,
    result.shape?.name,
    shapeName,
  ]);

  const visibleCandidates = candidates.slice(0, 3);
  const additionalCandidates = candidates.slice(3);
  const renderCandidateCard = (candidate, index) => {
    const candidateReadiness = candidate.details?.readiness ?? {};
    const selected = selectedCandidate?.id === candidate.id;
    const start = candidate.points_preview?.[0];
    const recommendedStart = candidates[0]?.points_preview?.[0];
    const startDistance = start && recommendedStart ? distanceMetres(start, recommendedStart) : null;
    return (
      <div className="candidate-option" role="listitem" key={candidate.id}>
      <button
        type="button"
        data-candidate-id={candidate.id}
        className={`candidate-card${selected ? " candidate-card--selected" : ""}`}
        aria-pressed={selected}
        onClick={() => chooseCandidate(candidate.id)}
      >
        <span className="candidate-card-topline">
          <span>{index === 0 ? "Recommended" : `Alternative ${index}`}</span>
          <b className={candidate.verification?.passed ? "status-good" : "status-warn"}>
            {candidate.verification?.passed ? "Ready to download" : "Check first"}
          </b>
        </span>
        <strong>
          {index === 0 ? "Best overall route" : normaliseLabel(candidate.shape_name)}
        </strong>
        <span className="candidate-location">
          {index === 0 ? "Highest-ranked measured option" : Number.isFinite(startDistance)
            ? startDistance >= 500
              ? `Starts ${(startDistance / 1000).toFixed(1)} km from the recommended start`
              : "Nearby start; compare the outline on the map"
            : "Compare this outline on the map"}
        </span>
        <span className="candidate-card-metrics">
          <span><b>{formatPercent(candidate.validation?.shape_fidelity)}</b> likeness</span>
          <span><b>{formatMetric(candidate.distance_km)}</b> km</span>
          <span>
            <b>
              {Number.isFinite(candidateReadiness.elevation_gain_m)
                ? `${Math.round(candidateReadiness.elevation_gain_m)} m`
                : "n/a"}
            </b>{" "}
            climb
          </span>
        </span>
      </button>
      </div>
    );
  };

  return (
    <section
      ref={focusRef}
      className="result"
      id="route-result"
      aria-labelledby="result-title"
      tabIndex="-1"
    >
      <div className="section-heading">
        <div>
          <h1 id="result-title">
            {shapeName} in {city}
          </h1>
        </div>
        <div className="result-heading-actions">
          <span
            className={`route-state route-state--${automaticChecksPassed ? "good" : "warn"}`}
          >
            <span aria-hidden="true">{automaticChecksPassed || userAccepted ? "✓" : "!"}</span>
            {stateLabel}
          </span>
          <a className="button button--quiet result-jump-link" href="#route-download">
            Download options
            <span aria-hidden="true">↓</span>
          </a>
        </div>
      </div>

      <section className="request-summary-card" aria-labelledby="request-summary-title">
        <div className="request-summary-heading">
          <div>
            <span className="eyebrow">Your request</span>
            <h2 id="request-summary-title">We understood {understoodDrawing}</h2>
            <p className="request-summary-essentials">
              {city} · {activity}
              {targetDistance != null ? ` · ${formatMetric(targetDistance)} km` : ""}
            </p>
          </div>
          <button type="button" className="button button--quiet" onClick={onEditRequest}>
            Change request
          </button>
        </div>
        <details
          className="request-summary-details"
          open={requestDetailsOpen}
          onToggle={(event) => setRequestDetailsOpen(event.currentTarget.open)}
        >
          <summary>Request details</summary>
          <dl className="request-summary-facts">
            <div>
              <dt>Drawing</dt>
              <dd>{understoodDrawing}</dd>
            </div>
            <div>
              <dt>Place</dt>
              <dd>{city}</dd>
            </div>
            <div>
              <dt>Activity</dt>
              <dd>{activity}</dd>
            </div>
            <div>
              <dt>Target</dt>
              <dd>{targetDistance != null ? `${formatMetric(targetDistance)} km` : "Planner default"}</dd>
            </div>
            {planningOptions.start_label && (
              <div>
                <dt>Start</dt>
                <dd>{planningOptions.start_label}</dd>
              </div>
            )}
            {Number.isFinite(planningOptions.start_direction_deg) && (
              <div>
                <dt>First direction</dt>
                <dd>{Math.round(planningOptions.start_direction_deg)}°</dd>
              </div>
            )}
            {enabledPreferences.length > 0 && (
              <div>
                <dt>Preferences</dt>
                <dd>{enabledPreferences.join(", ")}</dd>
              </div>
            )}
          </dl>
        </details>
        {shapeSource === "fallback" && (
          <p className="request-summary-warning" role="status">
            We understood the request, but could not make a reliable custom outline. Change the
            wording before using a labelled fallback.
          </p>
        )}
        {fitDecision?.substituted && (
          <p className="request-summary-warning" role="status">
            The selected route uses {shapeName} instead of {understoodDrawing}; the street-fit
            explanation appears below.
          </p>
        )}
      </section>

      <div className="result-layout">
        <div className="map-card">
          <section className="candidate-compare" aria-labelledby="route-options-title">
            <div className="candidate-compare-heading">
              <div>
                <span className="eyebrow">Recommended first</span>
                <h3 id="route-options-title">Route options</h3>
              </div>
              <span>
                {candidates.length > 0
                  ? `${candidateSummary.verified_count ?? candidateSummary.accepted_count ?? 0} ready · ${reviewCount} to review`
                  : "Closest route found"}
              </span>
            </div>
            {candidates.length > 0 ? (
              <>
                <p className="candidate-choice-help">Choose a card to compare its street route. Likeness measures the drawing; “Ready to download” means all automatic checks passed.</p>
                <div className="candidate-card-list" role="list">
                  {visibleCandidates.map(renderCandidateCard)}
                </div>
                <p className="candidate-selection" role="status">
                  Selected: {selectedCandidateId === candidates[0]?.id ? "recommended route" : `alternative ${candidates.findIndex((candidate) => candidate.id === selectedCandidateId)}`}.
                </p>
                {additionalCandidates.length > 0 && (
                  <details className="candidate-more">
                    <summary>
                      Show {additionalCandidates.length} more route{additionalCandidates.length === 1 ? "" : "s"}
                    </summary>
                    <div className="candidate-card-list candidate-card-list--more" role="list">
                      {additionalCandidates.map((candidate, index) =>
                        renderCandidateCard(candidate, index + visibleCandidates.length))}
                    </div>
                  </details>
                )}
              </>
            ) : (
              <p className="candidate-empty">Showing the closest route the planner found.</p>
            )}
            {(candidateSummary.preflight_count ?? result.preflight_count ?? 0) > 0 &&
              candidates.length > 0 && (
              <p className="candidate-search-proof">
                <strong>
                  {(candidateSummary.preflight_count ?? result.preflight_count).toLocaleString()} placements screened
                </strong>
                <span aria-hidden="true"> · </span>
                {candidateSummary.full_route_attempt_count ?? result.candidate_count ?? 0} street
                routes measured
                <span aria-hidden="true"> · </span>
                strongest {candidates.length} shown. The winner balances drawing likeness,
                distance, closure, and connected-street checks.
              </p>
            )}
          </section>

          {(activeRoute.points_preview ?? []).length > 0 ? (
            <Suspense
              fallback={
                <div className="route-map route-map--empty" role="status">
                  <span className="map-spinner" aria-hidden="true" />
                  <strong>Loading street route…</strong>
                </div>
              }
            >
              <RouteMap
                ref={mapCaptureRef}
                points={activeRoute.points_preview}
                idealPoints={activeRoute.ideal_preview ?? result.ideal_preview}
                landmarkPoints={
                  activeRoute.landmark_preview ?? result.landmark_preview
                }
                poiMarkers={sightMarkers}
                readinessConcerns={readinessConcerns}
                activeConcernCode={activeConcernCode}
                analysisSegments={labOverlay?.segments ?? []}
                streetCanvasCandidates={result.street_canvas ?? []}
                editPoints={controlPoints}
                shapeName={shapeName}
                roadRouted={Boolean(activeRoute.snapped)}
                accepted={exportReady}
                editing={editing}
                onEditPoint={handleEditPoint}
              />
            </Suspense>
          ) : (
            <div className="route-map route-map--empty" role="status">
              <strong>We couldn’t draw this route</strong>
              <span>Change the idea or choose another route.</span>
            </div>
          )}
          {roadRouted && (activeRoute.points_preview ?? []).length > 1 && (
            <Suspense fallback={null}>
              <RouteWalkthrough key={activeRouteId} points={activeRoute.points_preview} shapeName={shapeName} />
            </Suspense>
          )}
          <div className="route-editor" aria-label="Route editor">
            <div>
              <strong>Edit route</strong>
              <p>Move a numbered point, then apply the changes.</p>
            </div>
            <div className="editor-actions">
              <button
                type="button"
                className="button button--secondary"
                onClick={() => {
                  resetEditor();
                  setEditing((value) => !value);
                }}
              >
                {editing
                  ? editDirty
                    ? "Discard point changes"
                    : "Close editor"
                  : "Edit this route"}
              </button>
              {editing && (
                <>
                  <button
                    type="button"
                    className="button button--quiet"
                    onClick={resetEditor}
                    disabled={editBusy}
                  >
                    Start over
                  </button>
                  <button
                    type="button"
                    className="button button--primary"
                    onClick={rerouteEdited}
                    disabled={editBusy || repairBusy || controlPoints.length < 2}
                  >
                    {editBusy ? "Applying changes…" : "Apply changes"}
                  </button>
                </>
              )}
            </div>
            {editError && (
              <p className="editor-error" role="alert">
                {editError}
              </p>
            )}
            {editedRoute && (
              <p className="editor-success" role="status">
                Changes saved: {formatMetric(editedRoute.distance_km)} km.
                {editedRoute.verification?.passed
                  ? " The route follows streets and passed every check."
                  : " Check the highlighted items before downloading."}
              </p>
            )}
          </div>
          <div className="map-caption">
            {(activeRoute.ideal_preview ?? result.ideal_preview ?? []).length > 1 && (
              <span>
                <span className="legend-line legend-line--guide" aria-hidden="true" /> Original
                drawing
              </span>
            )}
            {(activeRoute.landmark_preview ?? result.landmark_preview ?? []).length > 0 && (
              <span>
                <span className="legend-dot legend-dot--landmark" aria-hidden="true" /> Key
                points
              </span>
            )}
            {mappedConcernCount > 0 && (
              <span>
                <span className="legend-line legend-line--concern" aria-hidden="true" /> Review
                section
              </span>
            )}
            {labOverlay?.type === "inkproof" && (
              <span><span className="legend-line legend-line--inkproof" aria-hidden="true" /> Fragile ink</span>
            )}
            <span>
              <span className="legend-dot legend-dot--start" aria-hidden="true" /> Start
            </span>
            <span>
              <span className="legend-dot legend-dot--finish" aria-hidden="true" /> Finish
            </span>
            <span className="point-count">
              {activeRoute.snapped
                ? `${(activeRoute.points_preview ?? []).length.toLocaleString()} of ${(
                    routingDetails.route_point_count ??
                    (activeRoute.points_preview ?? []).length
                  ).toLocaleString()} map points shown`
                : "Preview only. Not matched to streets"}
            </span>
          </div>
        </div>

        <div className="result-sidebar">
          <dl className="metrics">
            <MetricCard
              label="Distance"
              value={
                activeRoute.distance_km != null
                  ? `${formatMetric(activeRoute.distance_km)} km`
                  : "n/a"
              }
              detail={normaliseLabel(result.intent?.sport)}
            />
            <MetricCard
              label="Drawing likeness"
              value={formatPercent(validation?.shape_fidelity)}
              detail="Route against drawing"
              tone={validation?.shape_fidelity >= 0.7 ? "good" : "warn"}
            />
            <MetricCard
              label="Climb"
              value={
                Number.isFinite(routeReadiness.elevation_gain_m)
                  ? `${Math.round(routeReadiness.elevation_gain_m)} m`
                  : "n/a"
              }
              detail="Estimated elevation gain"
            />
          </dl>

          <RouteReadinessCard
            readiness={routeReadiness}
            roadRouted={Boolean(activeRoute.snapped)}
            activeConcernCode={activeConcernCode}
            onConcernSelect={(concernCode) => {
              setLabOverlay(null);
              setActiveConcernCode(concernCode);
            }}
          />

          {fitDecision && (
            <div className={`notice ${fitDecision.substituted ? "notice--success" : "notice--warning"}`}>
              <strong>
                {fitDecision.substituted
                  ? `${requestedShape} did not fit these streets. Here is a ${shapeName}`
                  : `Why this ${requestedShape} needs a closer look`}
              </strong>
              <ul className="decision-reasons">
                {(fitDecision.reasons ?? []).map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {(fitDecision.candidates_tested ?? []).length > 0 && (
                <p>
                  Other shapes tried:{" "}
                  {fitDecision.candidates_tested.map(normaliseLabel).join(", ")}.
                </p>
              )}
            </div>
          )}

          {result.suggested_shape && !fitDecision?.substituted && (
            <div className="notice notice--info">
              <strong>Suggested shape</strong>
              <p>{normaliseLabel(result.suggested_shape)}</p>
              {result.suggestion_reason && <p>{result.suggestion_reason}</p>}
            </div>
          )}

          {shapeSource === "llm" && !fitDecision?.substituted && (
            <div className="notice notice--info">
              <strong>Made from your idea</strong>
              <p>
                This outline was created for your description. We sketched{" "}
                {result.shape?.generated_candidate_count || "several"} different versions
                and kept the strongest route-friendly one.
              </p>
              {aiDrawingReview?.method?.startsWith("rendered-image") &&
              aiDrawingReview.score != null ? (
                <p>
                  {aiDrawingReview.independent
                    ? "A separate visual check"
                    : "A disclosed AI self-check"}{" "}
                  scored it {formatPercent(aiDrawingReview.score)}
                  {Number.isFinite(aiDrawingCueCount)
                    ? ` and found ${aiDrawingCueCount} of ${aiDrawingReview.cue_results.length} defining features`
                    : ""}.
                </p>
              ) : (
                <p>
                  Geometry checks passed. Compare the dashed drawing with the street route
                  before heading out.
                </p>
              )}
            </div>
          )}

          {shapeSource === "llm" && aiDrawingReview?.cue_results?.length > 0 && (
            <details className="route-facts ai-recognition-card">
              <summary>Drawing recognition details</summary>
              <p className="route-facts-intro">
                {aiDrawingReview.independent
                  ? "A separate visual check looks"
                  : "A disclosed AI self-check looks"}{" "}
                for the defining features in the finished outline, not just in the AI
                description.
              </p>
              <ul className="gate-list">
                {aiDrawingReview.cue_results.map((cue) => (
                  <li
                    key={cue.feature_id}
                    className={cue.present ? "gate--pass" : "gate--fail"}
                  >
                    <span className="gate-icon" aria-hidden="true">
                      {cue.present ? "✓" : "!"}
                    </span>
                    <span>
                      <strong>{normaliseLabel(cue.feature_id)}</strong>
                      {cue.reason && <small>{cue.reason}</small>}
                    </span>
                    <span className="gate-value">
                      {cue.score == null ? (cue.present ? "Found" : "Missing") : formatPercent(cue.score)}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {!automaticChecksPassed && (
            <div className="notice notice--warning" role="status">
              <strong>Review this route</strong>
              <p>
                {!activeRoute.snapped
                  ? "The line could not be matched to connected streets and may cross inaccessible areas."
                  : "The street route differs from the drawing or requested distance. Check the map before downloading."}
              </p>
            </div>
          )}

        </div>

      <div className="route-output">
          <details className="route-facts">
              <summary>Route details</summary>
              <p className="route-facts-intro">
                Street detour is the extra distance added by the road network. Average drift is
                the difference between the route and drawing.
              </p>
              {validation?.routed_semantic_review && (
                <section aria-label="Visual drawing review" className="score-explainer">
                  <strong>AI visual review</strong>
                  <p>Looks like: {validation.routed_semantic_review.recognized_subject}</p>
                  <p>{validation.routed_semantic_review.reason}</p>
                  {validation.routed_semantic_review.missing_features?.length > 0 && (
                    <p>Details to check: {validation.routed_semantic_review.missing_features.join(", ")}</p>
                  )}
                  <small>
                    An automated opinion of this route’s outline. Check whether you recognise
                    the drawing yourself.
                  </small>
                </section>
              )}
              {validation?.feature_measurements?.length > 0 && (
                <section aria-label="Drawing detail preservation" className="score-explainer">
                  <strong>Drawing details</strong>
                  <ul>
                    {validation.feature_measurements.map((feature) => (
                      <li key={feature.feature_id}>
                        {feature.label}: {feature.preserved ? "preserved" : "needs a closer look"}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
              <dl>
                <div>
                  <dt>Route quality score</dt>
                  <dd>{formatPercent(score)}</dd>
                </div>
                <div>
                  <dt>Drawing likeness</dt>
                  <dd>{formatPercent(validation?.shape_fidelity)}</dd>
                </div>
                <div>
                  <dt>Route options shown</dt>
                  <dd>{Number.isFinite(candidateSummary.shown_count) ? candidateSummary.shown_count : candidates.length}</dd>
                </div>
                <div>
                  <dt>Drawing</dt>
                  <dd>{shapeName}</dd>
                </div>
                {result.request_id && (
                  <div>
                    <dt>Route reference</dt>
                    <dd><code>{result.request_id}</code></dd>
                  </div>
                )}
                <div>
                  <dt>Activity</dt>
                  <dd>{normaliseLabel(routingDetails.activity)}</dd>
                </div>
                <div>
                  <dt>Follows streets</dt>
                  <dd>{routingDetails.street_matched ? "Yes" : "No"}</dd>
                </div>
                <div>
                  <dt>Map points / drawing points</dt>
                  <dd>
                    {routingDetails.route_point_count ?? "n/a"} / {routingDetails.guide_point_count ?? "n/a"}
                  </dd>
                </div>
                <div>
                  <dt>Route / requested distance</dt>
                  <dd>
                    {formatMetric(distanceDetails.actual_km)} km / {formatMetric(distanceDetails.target_km)} km
                  </dd>
                </div>
                <div>
                  <dt>Over / under your request</dt>
                  <dd>
                    {formatSigned(distanceDetails.difference_km, 2, " km")} ({formatSigned(
                      distanceDetails.difference_percent,
                      1,
                      "%",
                    )})
                  </dd>
                </div>
                <div>
                  <dt>Street detour</dt>
                  <dd>{formatMetric(distanceDetails.route_to_guide_ratio, 2)}×</dd>
                </div>
                <div>
                  <dt>Average drift from drawing</dt>
                  <dd>{formatPercent(deviationDetails.mean_outline_deviation_ratio)}</dd>
                </div>
                {activeRoute.closed && (
                  <div>
                    <dt>Start to finish gap</dt>
                    <dd>{formatMetric(routingDetails.closure_gap_m, 0)} m</dd>
                  </div>
                )}
                <div>
                  <dt>Rotation / size</dt>
                  <dd>
                    {formatMetric(placementDetails.rotation_deg, 1)}° / {formatMetric(
                      placementDetails.scale_m,
                      0,
                    )} m
                  </dd>
                </div>
                <div>
                  <dt>Moved north / east</dt>
                  <dd>
                    {formatSigned(placementDetails.lat_offset_m, 0, " m")} / {formatSigned(
                      placementDetails.lon_offset_m,
                      0,
                      " m",
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Starting position match</dt>
                  <dd>{formatPercent(placementDetails.preflight_score)}</dd>
                </div>
              </dl>
          </details>

          <div hidden={planningMode !== "group"}>
            <GroupWorkspace
              key={`group-${result.request_id ?? result.prompt}`}
              activeRoute={activeRoute}
              name={`${shapeName} in ${city}`}
              sport={result.intent?.sport}
              routePreferences={result.planning_options?.route_preferences ?? {}}
              eligible={exportReady && !exportBlockedByPendingEdits}
              saveFile={saveFile}
              onOpenCampaignBuilder={onOpenCampaignBuilder}
              renderCompletion={props => <ArtRescueCard {...props} />}
            />
          </div>
          <details
            className="route-lab"
            key={result.request_id ?? result.prompt}
          >
            <summary className="route-lab-heading">
              <div>
                <span className="eyebrow">Optional tools</span>
                <h3 id="route-lab-title">More tools for this route</h3>
              </div>
              <p>Plan the day, improve the drawing, or prepare a classroom activity.</p>
              <span className="route-lab-marker" aria-hidden="true">+</span>
            </summary>
            <div className="route-lab-grid">
              <h4 className="route-lab-group-title">Plan the day</h4>
              <TimedReadinessCard points={activeRoute.points_preview ?? []} />
              <NightReadinessCard
                key={`night-${activeRouteId}`}
                points={activeRoute.points_preview ?? []}
                overlayType={labOverlay?.type}
                onOverlayChange={updateLabOverlay}
              />
              <AccessibilityCard
                key={`accessibility-${activeRouteId}`}
                points={activeRoute.points_preview ?? []}
                overlayType={labOverlay?.type}
                onOverlayChange={updateLabOverlay}
              />
              <LandmarksCard
                key={`landmarks-${activeRouteId}`}
                points={activeRoute.points_preview ?? []}
                onMarkersChange={setSightMarkers}
              />

              <h4 className="route-lab-group-title">Improve the drawing</h4>
              <StreetCanvasCard candidates={result.street_canvas ?? []} />
              <InkproofCard
                key={`inkproof-${activeRouteId}`}
                points={activeRoute.points_preview ?? []}
                overlayType={labOverlay?.type}
                onOverlayChange={updateLabOverlay}
              />
              <section className="recognition-repair-card" aria-labelledby="repair-title">
                <div>
                  <span className="eyebrow">Drawing improvement</span>
                  <h3 id="repair-title">Make the outline read more clearly</h3>
                  <p>Re-route from the shape's strongest visual anchors and compare the result.</p>
                </div>
                <button
                  type="button"
                  className="button button--secondary"
                  onClick={improveRecognition}
                  disabled={repairBusy || editBusy || !(activeRoute.ideal_preview ?? []).length}
                >
                  {repairBusy ? "Refining..." : "Find a crisper version"}
                </button>
                {repairNotice && <p className="editor-success" role="status">{repairNotice}</p>}
              </section>

              <div className="personal-rescue" hidden={planningMode !== "personal"}>
                <h4 className="route-lab-group-title">Finish your drawing</h4>
                <ArtRescueCard
                  key={`rescue-${activeRouteId}`}
                  activeRoute={activeRoute}
                  shapeName={shapeName}
                  city={city}
                  sport={result.intent?.sport}
                  overlayType={labOverlay?.type}
                  onOverlayChange={updateLabOverlay}
                />
              </div>

              <h4 className="route-lab-group-title">Classroom resources</h4>
              <LessonPackCard
                key={`lesson-${activeRouteId}`}
                activeRoute={activeRoute}
                shapeName={shapeName}
                city={city}
                onOpen={openLessonSheet}
              />
            </div>
          </details>

          <div className={`export-card export-card--${automaticChecksPassed || userAccepted ? "ready" : "review"}`} id="route-download">
            <div>
              <h3>
                {!roadRouted
                  ? "Street route unavailable"
                  : exportReady
                    ? "Ready to download"
                    : `${verification?.failed_gates?.length ?? 1} item${verification?.failed_gates?.length === 1 ? "" : "s"} to check before download`}
              </h3>
            </div>
            {automaticChecksPassed && roadRouted && (
              <p className="export-decision-copy">
                This route follows connected streets and passed the drawing, distance, and closure checks.
              </p>
            )}
            {userAccepted && !automaticChecksPassed && roadRouted && (
              <p className="export-decision-copy">
                You reviewed this street route and approved it for download.
              </p>
            )}
            {!automaticChecksPassed && !userAccepted && roadRouted && (
              <p className="acceptance-copy">
                Review the highlighted difference on the map, or choose another route above.
              </p>
            )}
            <div className="download-actions">
              {!automaticChecksPassed && !userAccepted && roadRouted && activeRoute.gpx && (
                <button
                  type="button"
                  className="button button--primary accept-route-button"
                  onClick={() => {
                    setAcceptedRouteIds((current) => new Set(current).add(activeRouteId));
                    recordRouteAcceptance({
                      generation_request_id: result.request_id ?? null,
                      route_id: activeRouteId,
                      shape_name: activeRoute.shape_name ?? result.shape?.name ?? "route",
                      automatic_checks_passed: automaticChecksPassed,
                      snapped: Boolean(activeRoute.snapped),
                      failed_gates: verification?.failed_gates ?? [],
                      score: validation?.score ?? null,
                      shape_fidelity: validation?.shape_fidelity ?? null,
                      distance_km: activeRoute.distance_km ?? null,
                    }).catch(() => {
                      // Telemetry must never block the user's chosen export.
                    });
                    onDownload("gpx", activeRoute.gpx);
                  }}
                  disabled={exportBlockedByPendingEdits}
                >
                  Approve and download GPX
                </button>
              )}
              {exportReady && activeRoute.gpx && (
                <button
                  type="button"
                  className="button button--primary"
                  onClick={() => onDownload("gpx", activeRoute.gpx)}
                  disabled={exportBlockedByPendingEdits}
                >
                  {editedRoute?.gpx ? "Download edited GPX" : "Download GPX"}
                </button>
              )}
              {exportReady && activeRoute.tcx && (
                <button
                  type="button"
                  className="button button--secondary"
                  onClick={() => onDownload("tcx", activeRoute.tcx)}
                  disabled={exportBlockedByPendingEdits}
                >
                  Download TCX
                </button>
              )}
            </div>
            {roadRouted && (
              <details className="after-route-actions">
                <summary>More ways to use this route</summary>
                <div className="after-route-actions-body">
                  <div className="after-route-action-buttons">
                    <button
                      type="button"
                      className="button button--secondary gift-poster-button"
                      onClick={openGiftPoster}
                    >
                      Gift poster
                    </button>
                    <button
                      type="button"
                      className="button button--secondary"
                      onClick={(event) => {
                        event.currentTarget.focus({ preventScroll: true });
                        setReelOpen(true);
                      }}
                    >
                      Record reel
                    </button>
                  </div>
                  {posterError && (
                    <p className="editor-error" role="alert">{posterError}</p>
                  )}
                  {activeRoute.gpx && (
                    <details className="gpx-help">
                      <summary>Use this GPX with Garmin, Strava, or Komoot</summary>
                      <ol>
                        <li>Download the GPX file above.</li>
                        <li>Open your platform’s route or course import tool and select the file.</li>
                        <li>Check the imported map, direction, access, and surface before saving it.</li>
                      </ol>
                      <p>
                        Import wording differs by platform. Paceasso keeps the standard GPX
                        route portable and does not connect to your account.
                      </p>
                    </details>
                  )}
                </div>
              </details>
            )}
            {exportBlockedByPendingEdits && (
              <p className="pending-edit-note" role="status">
                Apply or discard your changes before downloading.
              </p>
            )}
            {activeRoute.gallery_publish_token && (!editedRoute || editedRoute.allow_gallery_share) && (
              <details className="gallery-publish">
                <summary>
                  <span>
                    <strong>Share map publicly</strong>
                    <small>Optional</small>
                  </span>
                </summary>
                <div className="gallery-publish-body">
                  <p>
                    Publishes the map, route line, location, and visible street names. Your prompt,
                    profile, and route file stay private.
                  </p>
                {!publishedAsset ? (
                  <>
                    <label>
                      <input
                        type="checkbox"
                        checked={galleryConsent}
                        onChange={(event) => setGalleryConsent(event.target.checked)}
                        disabled={galleryBusy}
                      />
                      I understand that this location and its street names will be public.
                    </label>
                    <button
                      type="button"
                      className="button button--secondary"
                      onClick={publishMapScreenshot}
                      disabled={!canPublishGallery || !galleryConsent || galleryBusy}
                    >
                      {galleryBusy ? "Publishing…" : "Publish map"}
                    </button>
                  </>
                ) : (
                  <p className="gallery-publish-success" role="status">
                    Map published. <a href="#gallery">View in gallery</a>.
                  </p>
                )}
                {galleryError && (
                  <p className="gallery-publish-error" role="alert">
                    {galleryError}
                  </p>
                )}
                {!exportReady && (
                  <small>Review or approve this route before sharing it.</small>
                )}
                {editing && <small>Finish editing before sharing the map.</small>}
                </div>
              </details>
            )}
            {!roadRouted && (
              <p className="export-unavailable">
                No GPS file was created because this preview is not matched to connected streets.
                Adjust the route or try again when street routing is available.
              </p>
            )}
            {roadRouted && !activeRoute.gpx && (
              <p className="export-unavailable">
                There isn’t enough route data to make a GPX file. Edit the route or create a new
                one, then try again.
              </p>
            )}
            <p className="safety-note">Before heading out, check current access, traffic, crossings, and surfaces.</p>
          </div>
      </div>
      </div>

      {(verification?.gates?.length > 0 || issueList.length > 0) && (
        <details className="detail-card route-review-box">
          <summary>
            Route issues &amp; items to check
            <span>{issueList.length + (verification?.failed_gates?.length ?? 0)}</span>
          </summary>
          <div className="route-review-body">
            <section className="route-issues" aria-labelledby="route-issues-title">
              <h3 id="route-issues-title">Route issues</h3>
              {issueList.length > 0 ? (
                <ul>
                  {issueList.map((issue) => <li key={issue}>{issue}</li>)}
                </ul>
              ) : (
                <p>No route issues reported.</p>
              )}
            </section>
            {verification?.gates?.length > 0 && (
              <section
                className={`verification-card verification-card--${automaticChecksPassed ? "pass" : "fail"}`}
                aria-labelledby="route-checks-title"
              >
                <div className="verification-heading">
                  <strong className="verification-title" id="route-checks-title">
                    {automaticChecksPassed
                      ? "Checks passed"
                      : `${verification.failed_gates?.length ?? 0} item${verification.failed_gates?.length === 1 ? "" : "s"} to check`}
                  </strong>
                  <span className="verification-count">
                    {verification.passed_count} of {verification.required_count} passed
                  </span>
                </div>
                <div className="verification-body">
                  <div className="score-explainer">
                    <strong>What the scores mean</strong>
                    <p>
                      Higher scores mean a closer match to the drawing. They do not measure traffic,
                      access, surface quality, or safety.
                    </p>
                  </div>
                  <ul className="gate-list">
                    {verification.gates
                      .filter((gate) => gate.applies)
                      .map((gate) => (
                        <li key={gate.key} className={gate.passed ? "gate--pass" : "gate--fail"}>
                          <span className="gate-icon" aria-hidden="true">
                            {gate.passed ? "✓" : "!"}
                          </span>
                          <span>
                            <strong>{GATE_COPY[gate.key]?.label ?? gate.label}</strong>
                            <small>{GATE_COPY[gate.key]?.description ?? gate.description}</small>
                            <small className="gate-interpretation">{explainGateResult(gate)}</small>
                          </span>
                          <span className="gate-value">
                            {formatGateValue(gate)}
                            {formatGateMinimum(gate) && <small>{formatGateMinimum(gate)}</small>}
                          </span>
                        </li>
                      ))}
                  </ul>
                </div>
              </section>
            )}
          </div>
        </details>
      )}

      <GiftPosterOverlay
        open={active && posterOpen}
        imageDataUrl={posterImage}
        dedication={posterDedication}
        onDedicationChange={setPosterDedication}
        onClose={() => setPosterOpen(false)}
        stats={[
          ["Drawing", shapeName],
          ["Distance", activeRoute.distance_km != null ? `${formatMetric(activeRoute.distance_km)} km` : "n/a"],
          ["Activity", activity],
          ["Place", city],
          ["Planned", new Date().toLocaleDateString(undefined, { dateStyle: "long" })],
        ]}
      />

      <ReelOverlay
        open={active && reelOpen}
        onClose={() => setReelOpen(false)}
        startCapture={startReelCapture}
        shapeName={shapeName}
        city={city}
      />

      <LessonSheetOverlay
        open={active && lessonOpen}
        pack={lessonPack}
        points={lessonPoints}
        dedication={lessonDedication}
        onDedicationChange={setLessonDedication}
        onClose={() => setLessonOpen(false)}
      />
    </section>
  );
}

export default function App() {
  const [planningMode, setPlanningMode] = useState(() =>
    typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mode") === "group" ? "group" : "personal",
  );
  const changePlanningMode = mode => {
    setPlanningMode(mode);
    const url = new URL(window.location.href);
    if (mode === "group") url.searchParams.set("mode", "group");
    else url.searchParams.delete("mode");
    window.history.replaceState(null, "", url);
  };
  const [prompt, setPrompt] = useState(INITIAL_EXAMPLE_PROMPT);
  const [ideaDeck, setIdeaDeck] = useState(() => shuffled(QUICK_IDEAS));
  const [ideaRefreshCount, setIdeaRefreshCount] = useState(0);
  const [plannerStep, setPlannerStep] = useState("idea");
  const [appView, setAppView] = useState(() =>
    typeof window !== "undefined" && window.location.hash === "#gallery" ? "gallery" : "planner",
  );
  const [promptError, setPromptError] = useState("");
  const [promptValidationAttempt, setPromptValidationAttempt] = useState(0);
  const [ideaQuery, setIdeaQuery] = useState("");
  const [ideaCategory, setIdeaCategory] = useState("");
  const [ideaCatalogOpen, setIdeaCatalogOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingKind, setLoadingKind] = useState("route");
  const [generationProgress, setGenerationProgress] = useState(null);
  const [generationPreview, setGenerationPreview] = useState(null);
  const [imageUrl, setImageUrl] = useState("");
  const [imageCity, setImageCity] = useState(SUGGEST_CITIES[0]);
  const [imageSport, setImageSport] = useState("run");
  const [imageDistance, setImageDistance] = useState("10");
  const [imageErrors, setImageErrors] = useState({});
  const [svgUpload, setSvgUpload] = useState(null);
  const [mapTemplates, setMapTemplates] = useState(MAP_PLACEMENT_FALLBACK_SHAPES);
  const [mapShape, setMapShape] = useState("heart");
  const [mapCity, setMapCity] = useState(SUGGEST_CITIES[0]);
  const [mapSport, setMapSport] = useState("run");
  const [mapDistance, setMapDistance] = useState("10");
  const [mapPlacementPreview, setMapPlacementPreview] = useState(null);
  const [mapPlacementOpen, setMapPlacementOpen] = useState(false);
  const [mapPlacementBusy, setMapPlacementBusy] = useState(false);
  const [mapPlacementError, setMapPlacementError] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [suggestCity, setSuggestCity] = useState(SUGGEST_CITIES[0]);
  const [suggestSport, setSuggestSport] = useState("run");
  const [suggestDistance, setSuggestDistance] = useState("10");
  const [suggestErrors, setSuggestErrors] = useState({});
  const [suggestNotice, setSuggestNotice] = useState("");
  const [startMode, setStartMode] = useState("any");
  const [startAddress, setStartAddress] = useState("");
  const [startPoint, setStartPoint] = useState(null);
  const [startDirection, setStartDirection] = useState("");
  const [routePreferences, setRoutePreferences] = useState(() => ({
    ...EMPTY_ROUTE_PREFERENCES,
  }));
  const [locationBusy, setLocationBusy] = useState(false);
  const [locationError, setLocationError] = useState("");
  const [interpretation, setInterpretation] = useState(null);
  const [interpretationBusy, setInterpretationBusy] = useState(false);
  const [interpretationError, setInterpretationError] = useState("");
  const [downloadNotice, setDownloadNotice] = useState("");
  const [galleryRefreshKey, setGalleryRefreshKey] = useState(0);
  const [lastPublishedGalleryAsset, setLastPublishedGalleryAsset] = useState(null);
  const [featuredGallery, setFeaturedGallery] = useState({ asset: null, status: "loading" });
  const requestRef = useRef(null);
  const interpretationRef = useRef(null);
  const interpretationPromptSyncRef = useRef(null);
  const mapPreviewRef = useRef(null);
  const mapTemplatesRequestedRef = useRef(false);
  const lastGenerationRef = useRef(null);
  const featuredGalleryLoadedRef = useRef(false);
  const loadingRef = useRef(null);
  const resultRef = useRef(null);
  const errorRef = useRef(null);
  const promptRef = useRef(null);
  const focusedPromptNodesRef = useRef(new WeakSet());
  const reviewHeadingRef = useRef(null);
  const alternateStartsRef = useRef(null);
  const mapStartRef = useRef(null);
  const fieldsStartRef = useRef(null);
  const imageStartRef = useRef(null);
  const setPromptRef = useCallback((node) => {
    promptRef.current = node;
    // React's development checks can attach the same ref twice. Never take
    // focus back from a user who has already moved on to the next control.
    if (node && !focusedPromptNodesRef.current.has(node)) {
      focusedPromptNodesRef.current.add(node);
      node.focus({ preventScroll: true });
    }
  }, []);
  const promptSubmitPointerRef = useRef(false);
  const suggestCityRef = useRef(null);
  const suggestActivityRef = useRef(null);
  const suggestDistanceRef = useRef(null);
  const imageUrlRef = useRef(null);
  const imageCityRef = useRef(null);
  const imageSportRef = useRef(null);
  const imageDistanceRef = useRef(null);
  const [campaign, setCampaign] = useState(() => parseCampaignFromUrl());
  const [campaignBuilderOpen, setCampaignBuilderOpen] = useState(false);
  const campaignApplied = useRef(false);

  useEffect(() => {
    const syncViewFromHash = () => {
      setAppView(window.location.hash === "#gallery" ? "gallery" : "planner");
      if (window.location.hash === "#route-result" && result) showCurrentRoute();
    };
    window.addEventListener("hashchange", syncViewFromHash);
    return () => window.removeEventListener("hashchange", syncViewFromHash);
  }, [result]);

  useEffect(() => {
    if (appView !== "planner" || featuredGalleryLoadedRef.current) return undefined;
    const controller = new AbortController();
    listGallery({ limit: 50, signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        const asset = chooseFeaturedGalleryAsset(response.assets);
        setFeaturedGallery({
          asset,
          status: asset ? "ready" : response.configured === false ? "unavailable" : "empty",
        });
        featuredGalleryLoadedRef.current = true;
      })
      .catch((galleryError) => {
        if (controller.signal.aborted || galleryError?.name === "AbortError") return;
        setFeaturedGallery({ asset: null, status: "unavailable" });
        featuredGalleryLoadedRef.current = true;
      });
    return () => controller.abort();
  }, [appView]);

  useEffect(() => {
    if (!campaign || campaignApplied.current) return;
    // A campaign link pre-fills the drawing once; supporters can still edit it.
    campaignApplied.current = true;
    const activityWord = distanceLimits(suggestSport).activity;
    setPrompt(`${campaign.shape} in ${suggestCity}, ${activityWord}, about ${suggestDistance} km`);
    setPromptError("");
  }, [campaign, suggestCity, suggestSport, suggestDistance]);

  useEffect(() => {
    if (!result || appView !== "planner" || plannerStep !== "result" || loading) return;
    const focusFrame = window.requestAnimationFrame(() => {
      resultRef.current?.focus({ preventScroll: true });
      // `auto` inherits the page's smooth scrolling. Finish the stage change
      // before the user starts interacting with controls further down the page.
      resultRef.current?.scrollIntoView({ behavior: "instant", block: "start" });
    });
    return () => window.cancelAnimationFrame(focusFrame);
  }, [result, appView, plannerStep, loading]);

  useEffect(() => {
    if (appView === "planner" && plannerStep === "review" && !loading) {
      reviewHeadingRef.current?.focus();
    }
  }, [appView, plannerStep, loading]);

  useEffect(() => {
    if (!loading) return undefined;

    const mobileViewport = window.matchMedia("(max-width: 48rem)").matches;
    if (mobileViewport) return undefined;

    const focusFrame = window.requestAnimationFrame(() => {
      loadingRef.current?.focus({ preventScroll: true });
      loadingRef.current?.scrollIntoView({ behavior: "instant", block: "start" });
    });

    return () => window.cancelAnimationFrame(focusFrame);
  }, [loading]);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  useEffect(() => {
    if (!(promptValidationAttempt > 0 && promptError)) return undefined;
    // Run after the pointer-submit default action. Safari/WebKit otherwise
    // clears a focus applied during React's discrete-event effect flush.
    const timer = window.setTimeout(() => {
      promptRef.current?.focus({ preventScroll: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [promptError, promptValidationAttempt]);

  useEffect(
    () => () => {
      requestRef.current?.abort();
      interpretationRef.current?.abort();
      mapPreviewRef.current?.abort();
    },
    [],
  );

  useEffect(() => {
    if (interpretationPromptSyncRef.current === prompt) {
      interpretationPromptSyncRef.current = null;
      return;
    }
    interpretationPromptSyncRef.current = null;
    interpretationRef.current?.abort();
    interpretationRef.current = null;
    setInterpretation(null);
    setInterpretationError("");
    setInterpretationBusy(false);
  }, [prompt]);

  const activeIdea = useMemo(
    () => QUICK_IDEAS.find((idea) => idea.prompt === prompt)?.label,
    [prompt],
  );
  const filteredIdeas = useMemo(() => {
    const query = ideaQuery.trim().toLocaleLowerCase("en");
    return QUICK_IDEAS.filter((idea) =>
      (!ideaCategory || idea.category === ideaCategory) &&
      `${idea.label} ${idea.category}`.toLocaleLowerCase("en").includes(query),
    );
  }, [ideaQuery, ideaCategory]);

  function showCurrentRoute() {
    interpretationRef.current?.abort();
    interpretationRef.current = null;
    setInterpretationBusy(false);
    setAppView("planner");
    setPlannerStep("result");
  }

  function chooseExample(idea) {
    setPrompt(idea.prompt);
    setPromptError("");
    window.requestAnimationFrame(() => promptRef.current?.focus());
  }

  function openStartPanel(panelRef) {
    if (!alternateStartsRef.current || !panelRef.current) return;
    alternateStartsRef.current.open = true;
    panelRef.current.open = true;
    window.requestAnimationFrame(() => {
      panelRef.current?.querySelector("summary")?.focus();
      panelRef.current?.scrollIntoView({ block: "start", behavior: "auto" });
    });
  }

  async function loadMapPlacementTemplates() {
    if (mapTemplatesRequestedRef.current) return;
    mapTemplatesRequestedRef.current = true;
    try {
      const response = await fetchShapeTemplates();
      const templates = (response?.shapes ?? []).filter(
        (item) => typeof item?.id === "string" && typeof item?.label === "string",
      );
      if (templates.length > 0) setMapTemplates(templates);
    } catch {
      // The popular fallback list keeps this optional flow usable.
    }
  }

  async function openMapPlacement(event) {
    event.preventDefault();
    if (loading || mapPlacementBusy) return;
    const { errors, numericDistance } = validateSuggestion({
      city: mapCity,
      sport: mapSport,
      distance: mapDistance,
    });
    if (!mapTemplates.some((item) => item.id === mapShape)) {
      errors.shape = "Choose a shape from the list.";
    }
    if (Object.keys(errors).length > 0) {
      setMapPlacementError(Object.values(errors)[0]);
      return;
    }

    const controller = new AbortController();
    mapPreviewRef.current?.abort();
    mapPreviewRef.current = controller;
    setMapPlacementBusy(true);
    setMapPlacementError("");
    try {
      const preview = await fetchShapePlacementPreview(
        {
          shape: mapShape,
          city: mapCity,
          sport: mapSport,
          distanceKm: numericDistance,
        },
        { signal: controller.signal },
      );
      setMapPlacementPreview(preview);
      setMapPlacementOpen(true);
    } catch (requestError) {
      if (requestError.name !== "AbortError") {
        setMapPlacementError(
          requestError.message || "We couldn’t prepare the placement map. Try again.",
        );
      }
    } finally {
      if (mapPreviewRef.current === controller) {
        mapPreviewRef.current = null;
        setMapPlacementBusy(false);
      }
    }
  }

  async function confirmMapPlacement(placement) {
    if (!mapPlacementPreview || loading || mapPlacementBusy) return;
    const activityWord = distanceLimits(mapSport).activity;
    const routePrompt = `${mapShape} in ${mapCity}, ${activityWord}, about ${mapDistance} km`;
    setPrompt(routePrompt);
    setPromptError("");
    setMapPlacementBusy(true);
    setMapPlacementOpen(false);
    try {
      await generate(
        routePrompt,
        buildPlanningPayload(
          { map_placement: placement },
          { includeStartConstraints: false },
        ),
      );
    } finally {
      setMapPlacementBusy(false);
    }
  }

  function buildPlanningPayload(
    extraPayload = {},
    { includeStartConstraints = true } = {},
  ) {
    return buildRouteSetupPayload(
      {
        startMode,
        startAddress,
        startPoint,
        startDirection,
        routePreferences,
      },
      extraPayload,
      { includeStartConstraints },
    );
  }

  const generate = useCallback(async (nextPrompt, extraPayload = {}) => {
    const cleanPrompt = normaliseRoutePrompt(nextPrompt);
    if (!cleanPrompt) return;

    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    lastGenerationRef.current = { prompt: cleanPrompt, payload: extraPayload };
    setLoadingKind(
      extraPayload.reference_image_url || extraPayload.reference_svg ? "image" : "route",
    );
    setLoading(true);
    setGenerationProgress(null);
    setGenerationPreview(null);
    setError(null);
    setResult(null);

    try {
      const response = await generateRoute(cleanPrompt, {
        signal: controller.signal,
        payload: extraPayload,
        onUpdate: (update) => {
          if (requestRef.current !== controller) return;
          if (update.type === "preview") {
            setGenerationPreview(update);
          } else if (update.type === "progress") {
            setGenerationProgress((previous) => {
              const polishPhase = update.phase
                ?? (update.stage?.startsWith("polish.") ? update.stage : null);
              const completed = previous?.polishCompleted ?? [];
              const polishCompleted = update.stage?.startsWith("polish.")
                && update.status === "completed" && !completed.includes(update.stage)
                ? [...completed, update.stage]
                : completed;
              return {
                ...update,
                llm_call: update.llm_call ?? previous?.llm_call ?? null,
                receivedAt: Date.now(),
                preflight_count: Math.max(previous?.preflight_count ?? 0, update.preflight_count ?? 0),
                search_areas: Math.max(previous?.search_areas ?? 0, update.search_areas ?? 0),
                connected_candidates: Math.max(previous?.connected_candidates ?? 0, update.connected_candidates ?? 0),
                routing_requests: Object.fromEntries(
                  [...new Set([...Object.keys(previous?.routing_requests ?? {}), ...Object.keys(update.routing_requests ?? {})])]
                    .map((key) => [key, Math.max(previous?.routing_requests?.[key] ?? 0, update.routing_requests?.[key] ?? 0)]),
                ),
                stageIndex: Math.max(previous?.stageIndex ?? -1, loadingStageIndex(update.stage)),
                polishStarted: Boolean(previous?.polishStarted || polishPhase),
                polishPhase,
                polishPhaseStartedAt: polishPhase
                  ? (polishPhase === previous?.polishPhase
                    ? previous.polishPhaseStartedAt
                    : Date.now())
                  : null,
                polishCompleted,
              };
            });
          }
        },
      });
      setResult(response);
      setPlannerStep("result");
    } catch (generationError) {
      if (generationError.name !== "AbortError") {
        setError({
          message:
            generationError.message ||
            "We couldn’t make that route. Check the idea and try again.",
          status: generationError.status ?? null,
          requestId: generationError.requestId ?? null,
          retryAfter: generationError.retryAfter ?? null,
          category: generationError.category ?? "request",
          retryable: generationError.retryable !== false,
        });
      }
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }, []);

  const focusPrompt = useCallback(() => {
    setAppView("planner");
    setPlannerStep("idea");
    window.requestAnimationFrame(() => {
      document.querySelector("#route-designer")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
      promptRef.current?.focus();
    });
  }, []);

  async function checkInterpretation({ advance = false, promptValue = prompt } = {}) {
    if (loading || interpretationBusy) return;
    const validation = validateRoutePrompt(promptValue);
    setPromptError(validation.error);
    if (validation.error) {
      setPromptValidationAttempt((current) => current + 1);
      window.setTimeout(() => promptRef.current?.focus({ preventScroll: true }), 0);
      return;
    }
    if (validation.value !== prompt) {
      interpretationPromptSyncRef.current = validation.value;
      setPrompt(validation.value);
    }

    const controller = new AbortController();
    interpretationRef.current?.abort();
    interpretationRef.current = controller;
    setInterpretationBusy(true);
    setInterpretationError("");
    setInterpretation(null);
    try {
      const response = await interpretRoute(validation.value, {
        signal: controller.signal,
        payload: buildPlanningPayload(),
      });
      setInterpretation(response);
      if (advance) setPlannerStep("review");
    } catch (requestError) {
      if (requestError.name !== "AbortError") {
        setInterpretationError(
          requestError.message || "We couldn’t check the request. You can still find routes.",
        );
        if (advance) setPlannerStep("review");
      }
    } finally {
      if (interpretationRef.current === controller) {
        interpretationRef.current = null;
        setInterpretationBusy(false);
      }
    }
  }

  function changeStartMode(nextMode) {
    setStartMode(nextMode);
    setLocationError("");
    if (nextMode === "any") {
      setStartAddress("");
      setStartPoint(null);
    } else if (nextMode === "address") {
      setStartPoint(null);
    } else if (nextMode === "location") {
      setStartAddress("");
    }
  }

  function changeStartAddress(value) {
    setStartMode("address");
    setStartAddress(value);
    setStartPoint(null);
    setLocationError("");
  }

  function changeRoutePreference(key, checked) {
    setRoutePreferences((current) => ({ ...current, [key]: checked }));
  }

  function resetRouteSetup() {
    setStartMode("any");
    setStartAddress("");
    setStartPoint(null);
    setStartDirection("");
    setRoutePreferences({ ...EMPTY_ROUTE_PREFERENCES });
    setLocationError("");
  }

  function selectCurrentLocation() {
    if (!navigator.geolocation) {
      setLocationError("This browser does not provide location access. Enter an address instead.");
      return;
    }
    setLocationBusy(true);
    setLocationError("");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setStartMode("location");
        setStartPoint({
          latitude: Number(position.coords.latitude.toFixed(6)),
          longitude: Number(position.coords.longitude.toFixed(6)),
          label: "Current location",
        });
        setStartAddress("");
        setLocationBusy(false);
      },
      (locationRequestError) => {
        setLocationBusy(false);
        setLocationError(
          locationRequestError.code === 1
            ? "Location access was not allowed. Enter an address or change browser permission."
            : "Your location could not be found. Enter an address instead.",
        );
      },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 },
    );
  }

  function handleSubmit(event) {
    event.preventDefault();
    promptSubmitPointerRef.current = false;
    if (loading) return;

    const validation = validateRoutePrompt(prompt);
    setPromptError(validation.error);
    if (validation.error) {
      setPromptValidationAttempt((current) => current + 1);
      window.setTimeout(() => promptRef.current?.focus({ preventScroll: true }), 0);
      return;
    }

    setPrompt(validation.value);
    if (plannerStep === "review") {
      generate(validation.value, buildPlanningPayload(
        interpretation?.intent ? { intent_override: interpretation.intent } : {},
      ));
      return;
    }
    checkInterpretation({ advance: true });
  }

  function handleSuggest(event) {
    event.preventDefault();
    if (loading) return;

    const { errors, numericDistance } = validateSuggestion({
      city: suggestCity,
      sport: suggestSport,
      distance: suggestDistance,
    });
    setSuggestErrors(errors);
    if (Object.keys(errors).length > 0) {
      if (errors.city) suggestCityRef.current?.focus();
      else if (errors.sport) suggestActivityRef.current?.focus();
      else suggestDistanceRef.current?.focus();
      return;
    }

    const suggestionPrompt = `suggest a ${suggestSport} route in ${suggestCity}, about ${numericDistance} km`;
    setPrompt(suggestionPrompt);
    setPromptError("");
    generate(suggestionPrompt, buildPlanningPayload());
  }

  function handleImageImport(event) {
    event.preventDefault();
    if (loading) return;

    const checkedUrl = svgUpload
      ? { value: "", error: "" }
      : validateImageReferenceUrl(imageUrl);
    const { errors: selectionErrors, numericDistance } = validateSuggestion({
      city: imageCity,
      sport: imageSport,
      distance: imageDistance,
    });
    const nextErrors = { ...selectionErrors, url: checkedUrl.error };
    setImageErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) {
      if (nextErrors.url) imageUrlRef.current?.focus();
      else if (nextErrors.city) imageCityRef.current?.focus();
      else if (nextErrors.sport) imageSportRef.current?.focus();
      else imageDistanceRef.current?.focus();
      return;
    }

    const { activity } = distanceLimits(imageSport);
    const imagePrompt = `a custom image in ${imageCity}, ${activity}, about ${numericDistance} km`;
    setPrompt(imagePrompt);
    setPromptError("");
    if (svgUpload) {
      generate(imagePrompt, buildPlanningPayload({
        reference_svg: svgUpload.svgText,
        reference_svg_name: svgUpload.filename,
      }));
      return;
    }
    setImageUrl(checkedUrl.value);
    generate(imagePrompt, buildPlanningPayload({ reference_image_url: checkedUrl.value }));
  }

  function cancelGeneration() {
    requestRef.current?.abort();
  }

  function handleDownload(extension, content) {
    const routeName = safeFilePart(
      `${result?.shape?.name ?? "gps-art"}-${result?.intent?.city ?? "route"}`,
    );
    const contentType = extension === "gpx" ? "application/gpx+xml" : "application/vnd.garmin.tcx+xml";
    saveFile(`${routeName}.${extension}`, content, contentType);
    setDownloadNotice(`${extension.toUpperCase()} download started.`);
  }

  const plannerActions = (
    <div className="prompt-actions">
      <button
        type="submit"
        className="button button--primary generate-button"
        onPointerDown={() => {
          promptSubmitPointerRef.current = true;
        }}
        onPointerUp={() => {
          promptSubmitPointerRef.current = false;
        }}
        onPointerCancel={() => {
          promptSubmitPointerRef.current = false;
        }}
        disabled={loading || interpretationBusy}
      >
        <span>
          {interpretationBusy
            ? "Checking request…"
            : plannerStep === "review"
              ? "Find routes"
              : "Review request"}
        </span>
        <span className="button-arrow" aria-hidden="true">→</span>
      </button>
      {plannerStep === "review" && (
        <button
          type="button"
          className="button button--secondary request-check-button"
          onClick={() => {
            setPlannerStep("idea");
            window.requestAnimationFrame(() => promptRef.current?.focus());
          }}
          disabled={loading}
        >
          Back
        </button>
      )}
    </div>
  );

  const { minimum: minimumDistance, maximum: maximumDistance, activity: activityLabel } =
    distanceLimits(suggestSport);
  return (
    <div className="app-shell">
      <a className="skip-link" href={appView === "gallery" ? "#gallery" : loading ? "#route-generation" : plannerStep === "result" ? "#route-result" : "#route-designer"}>
        {appView === "gallery" ? "Skip to gallery" : loading ? "Skip to route progress" : plannerStep === "result" ? "Skip to your route" : "Skip to route planner"}
      </a>

      <header className="site-header">
        <a className="brand" href="/" aria-label="Paceasso GPS Art Wizzard home">
          <span className="brand-mark" aria-hidden="true">
            <svg className="brand-mark-svg" viewBox="0 0 48 48">
              <rect x="2" y="2" width="44" height="44" rx="13" />
              <path d="M24 38L10 27L8 10L20 17L28 17L40 10L38 27L24 38ZM28 24L31 22L34 24M19 29L24 32L29 29" />
              <circle cx="24" cy="38" r="2.5" />
              <circle cx="17" cy="23" r="2" />
            </svg>
          </span>
          <span className="brand-copy">
            <strong>Paceasso</strong>{" "}
            <small>GPS Art Wizzard</small>
          </span>
        </a>
        <nav aria-label="Primary navigation">
          <a
            href="#route-designer"
            aria-current={appView === "planner" && plannerStep !== "result" ? "page" : undefined}
            onClick={() => {
              setAppView("planner");
              setPlannerStep("idea");
            }}
          >
            Create route
          </a>
          <a
            href="#gallery"
            aria-current={appView === "gallery" ? "page" : undefined}
            onClick={() => setAppView("gallery")}
          >
            Gallery
          </a>
          {result && !loading && (
            <a
              href="#route-result"
              aria-current={appView === "planner" && plannerStep === "result" ? "page" : undefined}
              onClick={showCurrentRoute}
            >
              Your route
            </a>
          )}
        </nav>
      </header>

      {campaign && (
        <div className="campaign-banner" role="region" aria-label="Active art campaign">
          <div>
            <strong>{campaign.name}</strong>
            <span>
              {campaign.hashtag ? `${campaign.hashtag} · ` : ""}
              Drawing: {normaliseLabel(campaign.shape)}
              {campaign.until ? ` · runs until ${campaign.until}` : ""}
            </span>
          </div>
          <button
            type="button"
            className="campaign-banner-dismiss"
            aria-label="Dismiss campaign banner"
            onClick={() => setCampaign(null)}
          >
            ×
          </button>
        </div>
      )}

      <main>
        <fieldset className="planning-mode">
          <legend>Planning mode</legend>
          <div>
            <label><input type="radio" name="planning-mode" value="personal" checked={planningMode === "personal"} onChange={() => changePlanningMode("personal")} />Personal</label>
            <label><input type="radio" name="planning-mode" value="group" checked={planningMode === "group"} onChange={() => changePlanningMode("group")} />Group</label>
          </div>
        </fieldset>
        {appView === "planner" && !loading && plannerStep !== "result" && (
        <section
          className="workspace generator-stage"
          id="route-designer"
          aria-labelledby="designer-title"
        >
          <div className="planner-intro">
            <h1 id="designer-title">{planningMode === "group" ? "Create a drawing with your group." : "Create GPS art on real streets."}</h1>
            <p className="planner-intro-copy">
              {planningMode === "group"
                ? "Plan one connected street drawing, then assign distances that fit your group and combine the recorded outings. The route distance below is for the whole drawing."
                : "Describe a drawing and a place. We’ll fit it to connected streets, compare the strongest routes, and prepare the one you can actually follow."}
            </p>
            <ol className="planner-proof planner-progress" aria-label="Route creation progress">
              <li aria-current={plannerStep === "idea" ? "step" : undefined}>
                <strong>1</strong><span>Describe your idea</span>
              </li>
              <li aria-current={plannerStep === "review" ? "step" : undefined}>
                <strong>2</strong><span>Review the request</span>
              </li>
              <li><strong>3</strong><span>{planningMode === "group" ? "Choose and assign sections" : "Choose and download"}</span></li>
            </ol>
            <p className="planner-safety-note">
              You can compare the street route with the original drawing before downloading anything.
            </p>
            {planningMode === "group" && <a className="button button--secondary" href="#group-event-resume">Open a saved event</a>}
          </div>

          <div className="group-entry" id="group-event-resume" hidden={planningMode !== "group"}>
            <GroupWorkspace saveFile={saveFile} onOpenCampaignBuilder={() => setCampaignBuilderOpen(true)} renderCompletion={props => <ArtRescueCard {...props} />} />
          </div>
          <div className="designer-card">
            <div className="card-heading">
              <div>
                <span className="step-label">Step {plannerStep === "review" ? "2" : "1"} of 3</span>
                <h2 ref={reviewHeadingRef} tabIndex={-1}>{plannerStep === "review" ? "Check your request" : "Describe your route"}</h2>
                <p>
                  {plannerStep === "review"
                    ? "Confirm the essentials, then add any optional route preferences."
                    : "Include the drawing, city, activity, and approximate distance in one sentence."}
                </p>
              </div>
            </div>

            {plannerStep === "idea" && (
              <div className="planner-start-links" role="group" aria-label="Choose how to start">
                <p>Prefer a guided start?</p>
                <div>
                  {[
                    ["Suggest a shape", "planner-start-fields", fieldsStartRef],
                    ["Place on map", "planner-start-map", mapStartRef],
                    ["Use image link", "planner-start-image", imageStartRef],
                  ].map(([label, target, panelRef]) => (
                    <button key={target} type="button" aria-controls={target} onClick={() => openStartPanel(panelRef)}>
                      {label}<span aria-hidden="true">↓</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <form onSubmit={handleSubmit} noValidate>
              <div className="planner-idea-step" hidden={plannerStep !== "idea"}>
              <label className="field-label" htmlFor="route-prompt">
                Drawing and location
              </label>
              <div className="textarea-wrap">
                <textarea
                  id="route-prompt"
                  ref={setPromptRef}
                  value={prompt}
                  onChange={(event) => {
                    const nextPrompt = event.target.value;
                    setPrompt(nextPrompt);
                    if (promptError) setPromptError(validateRoutePrompt(nextPrompt).error);
                  }}
                  onBlur={(event) => {
                    const nextControl = event.relatedTarget;
                    if (
                      promptSubmitPointerRef.current ||
                      nextControl?.type === "submit" &&
                      nextControl.form === event.currentTarget.form
                    ) {
                      return;
                    }
                    setPromptError(validateRoutePrompt(prompt).error);
                  }}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      checkInterpretation({
                        advance: true,
                        promptValue: event.currentTarget.value,
                      });
                    }
                  }}
                  rows={3}
                  maxLength={PROMPT_LIMIT}
                  placeholder="A flying pig in Budapest, running, 10 km"
                  aria-describedby="prompt-help prompt-count"
                  aria-invalid={Boolean(promptError)}
                  aria-errormessage={promptError ? "prompt-error" : undefined}
                  disabled={loading}
                  required
                />
                <span id="prompt-count" className="character-count">
                  {prompt.length}/{PROMPT_LIMIT}
                </span>
              </div>
              <p
                id="prompt-help"
                className={`field-help${promptError ? " field-help--with-error" : ""}`}
              >
                Example: a flying pig in Budapest, running, 10 km. Any shape, letter, number, or
                original idea is welcome.
              </p>
              {promptError && (
                <p id="prompt-error" className="field-error" role="alert">
                  <span aria-hidden="true">!</span>
                  {promptError}
                </p>
              )}

              {plannerStep === "idea" && plannerActions}

              <fieldset className="idea-picker">
                <legend>Example requests <span className="optional-label">Optional</span></legend>
                <p className="field-help idea-example-help">Examples include a place, activity, and distance. Edit the sentence above to make it yours.</p>
                <div className="idea-list">
                  {ideaDeck.slice(0, 6).map((idea) => (
                    <button
                      type="button"
                      key={idea.label}
                      className="idea-chip"
                      aria-pressed={activeIdea === idea.label}
                      onClick={() => chooseExample(idea)}
                      disabled={loading}
                    >
                      <span aria-hidden="true">{ideaGlyph(idea)}</span>
                      {idea.label}
                    </button>
                  ))}
                </div>
                <div className="idea-refresh">
                  <button type="button" className="button button--quiet" disabled={loading}
                    onClick={() => {
                      setIdeaDeck((deck) => [...deck.slice(6), ...deck.slice(0, 6)]);
                      setIdeaRefreshCount((count) => count + 1);
                    }}>Show different ideas</button>
                  <span className="field-help" role="status">
                    {ideaRefreshCount > 0 && <span className="sr-only">Idea set {ideaRefreshCount + 1}. </span>}
                    {ideaRefreshCount > 0 ? "Six new ideas. Your request is unchanged." : "Browse ideas without changing your request."}
                  </span>
                </div>
              </fieldset>

              <OccasionStrip
                disabled={loading}
                city={suggestCity}
                onPick={(pick) => {
                  const pickedSport = ACTIVITY_OPTIONS.some(([value]) => value === pick.sport)
                    ? pick.sport
                    : suggestSport;
                  const activityWord = distanceLimits(pickedSport).activity;
                  const distance =
                    Number.isFinite(pick.distance_km) ? String(pick.distance_km) : suggestDistance;
                  const pickedCity = pick.city || suggestCity;
                  setPrompt(
                    `${pick.shape_prompt} in ${pickedCity}, ${activityWord}, about ${distance} km`,
                  );
                  setPromptError("");
                  promptRef.current?.focus();
                }}
              />

              <details
                className="idea-catalog"
                onToggle={(event) => setIdeaCatalogOpen(event.currentTarget.open)}
              >
                <summary>
                  <span>
                    <strong>More shapes, letters, and numbers</strong>
                    <small>{QUICK_IDEAS.length} ready-made options, or type your own idea above.</small>
                  </span>
                  <b aria-hidden="true">+</b>
                </summary>
                {ideaCatalogOpen && <div className="idea-groups">
                  <div className="idea-filter">
                    <label htmlFor="idea-filter">Filter options</label>
                    <input
                      id="idea-filter"
                      type="search"
                      value={ideaQuery}
                      onChange={(event) => setIdeaQuery(event.target.value)}
                      placeholder="Search by name or category"
                    />
                    <div className="idea-category-field">
                      <label htmlFor="idea-category">Category</label>
                      <select id="idea-category" value={ideaCategory} onChange={(event) => setIdeaCategory(event.target.value)}>
                        <option value="">All categories</option>
                        {IDEA_CATEGORIES.map((category) => <option key={category}>{category}</option>)}
                      </select>
                    </div>
                    <div className="idea-filter-status">
                      <span role="status">{filteredIdeas.length} option{filteredIdeas.length === 1 ? "" : "s"}</span>
                      {(ideaQuery || ideaCategory) && (
                        <button type="button" className="button button--quiet" onClick={() => {
                          setIdeaQuery("");
                          setIdeaCategory("");
                          document.getElementById("idea-filter")?.focus();
                        }}>Clear filters</button>
                      )}
                    </div>
                  </div>
                  {IDEA_CATEGORIES.map((category) => {
                    const categoryIdeas = filteredIdeas.filter((idea) => idea.category === category);
                    if (categoryIdeas.length === 0) return null;
                    return (
                      <section className="idea-group" key={category} aria-label={`${category} ideas`}>
                        <h3>{category}</h3>
                        <div className="idea-list">
                          {categoryIdeas.map((idea) => (
                            <button
                              type="button"
                              key={idea.label}
                              className="idea-chip"
                              aria-pressed={activeIdea === idea.label}
                              onClick={() => chooseExample(idea)}
                              disabled={loading}
                            >
                              <span aria-hidden="true">{ideaGlyph(idea)}</span>
                              {idea.label}
                            </button>
                          ))}
                        </div>
                      </section>
                    );
                  })}
                  {filteredIdeas.length === 0 && (
                    <p className="idea-empty" role="status">
                      No matching shapes. Clear the filters to browse again, or type your own idea above.
                    </p>
                  )}
                </div>}
              </details>
              </div>

              {plannerStep === "review" && (
                <section className="request-review-card" aria-labelledby="request-review-title">
                  <div className="request-review-heading">
                    <div>
                      <span className="eyebrow">Your request</span>
                      <h3 id="request-review-title">
                        {interpretation?.drawing_label
                          ? `We’ll plan ${interpretation.drawing_label}`
                          : "Your route idea is ready"}
                      </h3>
                    </div>
                    <button
                      type="button"
                      className="button button--quiet"
                      onClick={() => {
                        setPlannerStep("idea");
                        window.requestAnimationFrame(() => promptRef.current?.focus());
                      }}
                    >
                      Change idea
                    </button>
                  </div>
                  <p className="request-review-prompt">{prompt}</p>
                  <dl className="request-review-facts">
                    <div>
                      <dt>Place</dt>
                      <dd>
                        {interpretation?.intent?.city
                          ? normaliseLabel(interpretation.intent.city)
                          : "Planner default"}
                      </dd>
                    </div>
                    <div>
                      <dt>Activity</dt>
                      <dd>{activityLabel(interpretation?.intent?.sport)}</dd>
                    </div>
                    <div>
                      <dt>Distance</dt>
                      <dd>
                        {Number.isFinite(interpretation?.intent?.distance_km)
                          ? `${interpretation.intent.distance_km} km`
                          : "Planner default"}
                      </dd>
                    </div>
                  </dl>
                  {(interpretation?.clarifications ?? []).length > 0 && (
                    <div className="request-review-note" role="status">
                      <strong>Worth checking</strong>
                      <ul>
                        {interpretation.clarifications.map((item) => (
                          <li key={`${item.field}-${item.question}`}>{item.question}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {interpretationError && (
                    <p className="request-review-note" role="status">
                      We could not preview every field, but your complete sentence is saved and can
                      still be sent to the route planner.
                    </p>
                  )}
                </section>
              )}

              {plannerStep === "review" && <RouteSetupPanel
                loading={loading}
                locationBusy={locationBusy}
                locationError={locationError}
                routePreferences={routePreferences}
                startAddress={startAddress}
                startDirection={startDirection}
                startMode={startMode}
                startPoint={startPoint}
                onCurrentLocation={selectCurrentLocation}
                onPreferenceChange={changeRoutePreference}
                onReset={resetRouteSetup}
                onStartAddressChange={changeStartAddress}
                onStartDirectionChange={setStartDirection}
                onStartModeChange={changeStartMode}
              />}

              {plannerStep === "review" && plannerActions}
            </form>

            {plannerStep === "idea" && (
            <details className="alternate-starts-panel" ref={alternateStartsRef}>
              <summary className="alternate-starts-heading">
                <span>
                  <strong>Other ways to start</strong>
                  <small>Place a shape yourself, use separate fields, or begin with an image.</small>
                </span>
                <b aria-hidden="true">+</b>
              </summary>
              <div className="alternate-start-options">

            <details
              className="map-placement-panel"
              id="planner-start-map"
              ref={mapStartRef}
              onToggle={(event) => {
                if (event.currentTarget.open) loadMapPlacementTemplates();
              }}
            >
              <summary>
                <span>
                  <strong>Place a shape on the map</strong>
                  <small>Choose an outline, then move, size, and rotate it before routing.</small>
                </span>
                <b aria-hidden="true">+</b>
              </summary>
              <form className="map-placement-form" onSubmit={openMapPlacement} noValidate>
                <div className="map-placement-fields">
                  <div className="field">
                    <label htmlFor="map-placement-shape">Shape</label>
                    <select
                      id="map-placement-shape"
                      value={mapShape}
                      onChange={(event) => {
                        setMapShape(event.target.value);
                        setMapPlacementError("");
                      }}
                      disabled={loading || mapPlacementBusy}
                    >
                      {mapTemplates.map((shape) => (
                        <option key={shape.id} value={shape.id}>{shape.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="map-placement-city">Map city</label>
                    <select
                      id="map-placement-city"
                      value={mapCity}
                      onChange={(event) => {
                        setMapCity(event.target.value);
                        setMapPlacementError("");
                      }}
                      disabled={loading || mapPlacementBusy}
                    >
                      {SUGGEST_CITY_GROUPS.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.cities.map((cityName) => (
                            <option key={cityName} value={cityName}>{cityName}</option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </div>
                  <fieldset className="field field--activity">
                    <legend>Activity</legend>
                    <div className="activity-options">
                      {ACTIVITY_OPTIONS.map(([value, label]) => (
                        <label className="activity-option" key={value}>
                          <input
                            type="radio"
                            name="map-placement-activity"
                            value={value}
                            checked={mapSport === value}
                            onChange={() => {
                              setMapSport(value);
                              setMapPlacementError("");
                              if (value === "bike" && Number(mapDistance) < 10) {
                                setMapDistance("10");
                              }
                            }}
                            disabled={loading || mapPlacementBusy}
                          />
                          <span>{label}</span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <div className="field field--distance">
                    <label htmlFor="map-placement-distance">Target distance</label>
                    <div className="input-suffix">
                      <input
                        id="map-placement-distance"
                        type="number"
                        inputMode="decimal"
                        min={distanceLimits(mapSport).minimum}
                        max={distanceLimits(mapSport).maximum}
                        step="1"
                        value={mapDistance}
                        onChange={(event) => {
                          setMapDistance(event.target.value);
                          setMapPlacementError("");
                        }}
                        disabled={loading || mapPlacementBusy}
                      />
                      <span>km</span>
                    </div>
                  </div>
                </div>
                <button
                  type="submit"
                  className="button button--secondary map-placement-open"
                  disabled={loading || mapPlacementBusy}
                >
                  {mapPlacementBusy ? "Preparing map…" : "Open placement map"}
                </button>
                <p className="map-placement-note">
                  Your position is a starting area, not a straight-line GPX. Nearby street fits are
                  measured before any route is offered.
                </p>
                {mapPlacementError && (
                  <p className="field-error" role="alert">
                    <span aria-hidden="true">!</span>
                    {mapPlacementError}
                  </p>
                )}
              </form>
            </details>

            <details className="suggest-panel" id="planner-start-fields" ref={fieldsStartRef}>
              <summary>
                <span>
                  <strong>Choose city, activity, and distance</strong>
                  <small>We’ll suggest a shape that suits your route.</small>
                </span>
                <b aria-hidden="true">+</b>
              </summary>
              <form className="suggest-form" onSubmit={handleSuggest} noValidate>
                <div className="suggest-fields">
                  <div className="field">
                    <label htmlFor="suggest-city">City</label>
                    <select
                      id="suggest-city"
                      ref={suggestCityRef}
                      value={suggestCity}
                      onChange={(event) => {
                        const nextCity = event.target.value;
                        setSuggestCity(nextCity);
                        if (suggestErrors.city) {
                          setSuggestErrors((current) => ({
                            ...current,
                            city: SUGGEST_CITIES.includes(nextCity)
                              ? ""
                              : "Choose a city from the list.",
                          }));
                        }
                      }}
                      aria-invalid={Boolean(suggestErrors.city)}
                      aria-errormessage={suggestErrors.city ? "suggest-city-error" : undefined}
                      disabled={loading}
                      required
                    >
                      {SUGGEST_CITY_GROUPS.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.cities.map((cityName) => (
                            <option key={cityName} value={cityName}>
                              {cityName}
                            </option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                    {suggestErrors.city && (
                      <p id="suggest-city-error" className="field-error" role="alert">
                        <span aria-hidden="true">!</span>
                        {suggestErrors.city}
                      </p>
                    )}
                  </div>
                  <fieldset
                    className="field field--activity"
                    aria-invalid={Boolean(suggestErrors.sport)}
                    aria-describedby={suggestErrors.sport ? "suggest-sport-error" : undefined}
                  >
                    <legend>Activity</legend>
                    <div className="activity-options">
                      {ACTIVITY_OPTIONS.map(([value, label]) => (
                        <label className="activity-option" key={value}>
                          <input
                            ref={value === "run" ? suggestActivityRef : undefined}
                            type="radio"
                            name="suggest-activity"
                            value={value}
                            checked={suggestSport === value}
                            onChange={() => {
                              setSuggestSport(value);
                              setSuggestErrors((current) => ({
                                ...current,
                                sport: "",
                                distance: "",
                              }));
                              if (value === "bike" && Number(suggestDistance) < 10) {
                                setSuggestDistance("10");
                                setSuggestNotice(
                                  "Cycling routes start at 10 km. Distance changed to 10 km.",
                                );
                              } else {
                                setSuggestNotice("");
                              }
                            }}
                            disabled={loading}
                            required
                          />
                          <span>{label}</span>
                        </label>
                      ))}
                    </div>
                    {suggestErrors.sport && (
                      <p id="suggest-sport-error" className="field-error" role="alert">
                        <span aria-hidden="true">!</span>
                        {suggestErrors.sport}
                      </p>
                    )}
                  </fieldset>
                  <div className="field field--distance">
                    <label htmlFor="suggest-distance">Distance</label>
                    <div className="input-suffix">
                      <input
                        id="suggest-distance"
                        ref={suggestDistanceRef}
                        type="number"
                        inputMode="decimal"
                        min={minimumDistance}
                        max={maximumDistance}
                        step="1"
                        value={suggestDistance}
                        onChange={(event) => {
                          const nextDistance = event.target.value;
                          setSuggestDistance(nextDistance);
                          setSuggestNotice("");
                          if (suggestErrors.distance) {
                            const nextValidation = validateSuggestion({
                              city: suggestCity,
                              sport: suggestSport,
                              distance: nextDistance,
                            });
                            setSuggestErrors((current) => ({
                              ...current,
                              distance: nextValidation.errors.distance ?? "",
                            }));
                          }
                        }}
                        onBlur={() => {
                          const nextValidation = validateSuggestion({
                            city: suggestCity,
                            sport: suggestSport,
                            distance: suggestDistance,
                          });
                          setSuggestErrors((current) => ({
                            ...current,
                            distance: nextValidation.errors.distance ?? "",
                          }));
                        }}
                        aria-describedby="suggest-distance-help"
                        aria-invalid={Boolean(suggestErrors.distance)}
                        aria-errormessage={
                          suggestErrors.distance ? "suggest-distance-error" : undefined
                        }
                        disabled={loading}
                        required
                      />
                      <span>km</span>
                    </div>
                    <p id="suggest-distance-help" className="field-hint">
                      {minimumDistance} to {maximumDistance} km for {activityLabel}.
                    </p>
                    {suggestErrors.distance && (
                      <p id="suggest-distance-error" className="field-error" role="alert">
                        <span aria-hidden="true">!</span>
                        {suggestErrors.distance}
                      </p>
                    )}
                  </div>
                </div>
                <p className="suggest-method">
                  We compare up to three shapes suited to these streets and this distance.
                </p>
                <div className="suggest-actions">
                  <p className="suggest-notice" aria-live="polite">
                    {suggestNotice}
                  </p>
                  <button
                    type="submit"
                    className="button button--secondary suggest-submit"
                    disabled={loading}
                  >
                    Find a route
                  </button>
                </div>
              </form>
            </details>

            <details className="image-reference-panel" id="planner-start-image" ref={imageStartRef}>
              <summary>
                <span>
                  <strong>Use your own SVG or an image link</strong>
                  <small>Upload an SVG outline, or let AI turn any supported image into GPS art.</small>
                </span>
                <b aria-hidden="true">+</b>
              </summary>
              <form className="image-reference-form" onSubmit={handleImageImport} noValidate>
                <SvgShapeUpload
                  value={svgUpload}
                  onChange={(upload) => {
                    setSvgUpload(upload);
                    if (upload) setImageErrors((current) => ({ ...current, url: "" }));
                  }}
                  disabled={loading}
                />
                <label className="image-url-field" htmlFor="image-reference-url">
                  <span>{svgUpload ? "Direct image URL (not used while an SVG is uploaded)" : "Or a direct image URL"}</span>
                  <input
                    id="image-reference-url"
                    ref={imageUrlRef}
                    type="url"
                    value={imageUrl}
                    onChange={(event) => {
                      setImageUrl(event.target.value);
                      if (imageErrors.url) {
                        setImageErrors((current) => ({
                          ...current,
                          url: validateImageReferenceUrl(event.target.value).error,
                        }));
                      }
                    }}
                    placeholder="https://example.com/drawing.avif"
                    aria-describedby="image-url-help"
                    aria-invalid={Boolean(imageErrors.url)}
                    aria-errormessage={imageErrors.url ? "image-url-error" : undefined}
                    disabled={loading || Boolean(svgUpload)}
                    required={!svgUpload}
                  />
                </label>
                <p id="image-url-help" className="field-help">
                  Paste a public image link. PNG, JPG, SVG, WebP, and other common formats are
                  supported, up to 5 MB. Use an image you have permission to reuse.
                </p>
                {imageErrors.url && (
                  <p id="image-url-error" className="field-error" role="alert">
                    <span aria-hidden="true">!</span>
                    {imageErrors.url}
                  </p>
                )}
                <div className="image-reference-fields">
                  <div className="field">
                    <label htmlFor="image-city">Destination</label>
                    <select
                      id="image-city"
                      ref={imageCityRef}
                      value={imageCity}
                      onChange={(event) => setImageCity(event.target.value)}
                      disabled={loading}
                    >
                      {SUGGEST_CITY_GROUPS.map((group) => (
                        <optgroup key={group.label} label={group.label}>
                          {group.cities.map((cityName) => (
                            <option key={cityName} value={cityName}>{cityName}</option>
                          ))}
                        </optgroup>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="image-activity">Travel mode</label>
                    <select
                      id="image-activity"
                      ref={imageSportRef}
                      value={imageSport}
                      onChange={(event) => setImageSport(event.target.value)}
                      disabled={loading}
                    >
                      {ACTIVITY_OPTIONS.map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="image-distance">Length</label>
                    <div className="distance-input">
                      <input
                        id="image-distance"
                        ref={imageDistanceRef}
                        type="number"
                        min={distanceLimits(imageSport).minimum}
                        max={distanceLimits(imageSport).maximum}
                        step="0.5"
                        value={imageDistance}
                        onChange={(event) => setImageDistance(event.target.value)}
                        disabled={loading}
                      />
                      <span>km</span>
                    </div>
                  </div>
                  <button type="submit" className="button button--secondary" disabled={loading}>
                    {loading
                      ? "Generating from image…"
                      : svgUpload ? "Generate route from SVG" : "Generate AI route"}
                  </button>
                </div>
                {(imageErrors.city || imageErrors.sport || imageErrors.distance) && (
                  <p className="field-error" role="alert">
                    <span aria-hidden="true">!</span>
                    {imageErrors.city || imageErrors.sport || imageErrors.distance}
                  </p>
                )}
              </form>
            </details>
              <div className="alternate-route-setup">
                <RouteSetupPanel
                  loading={loading}
                  locationBusy={locationBusy}
                  locationError={locationError}
                  routePreferences={routePreferences}
                  startAddress={startAddress}
                  startDirection={startDirection}
                  startMode={startMode}
                  startPoint={startPoint}
                  onCurrentLocation={selectCurrentLocation}
                  onPreferenceChange={changeRoutePreference}
                  onReset={resetRouteSetup}
                  onStartAddressChange={changeStartAddress}
                  onStartDirectionChange={setStartDirection}
                  onStartModeChange={changeStartMode}
                />
              </div>
              </div>
            </details>
            )}
          </div>

            <figure className="studio-art" aria-labelledby="studio-example-title">
              <div className="studio-art-heading">
                <span className="studio-eyebrow">From the public gallery</span>
                <h2 id="studio-example-title">See what the streets inspire.</h2>
              </div>
              {featuredGallery.asset ? (
                <a className="studio-map-link" href={featuredGallery.asset.image_url} target="_blank" rel="noreferrer" aria-label="Open this public gallery image in a new tab">
                  <img
                    src={featuredGallery.asset.thumbnail_url || featuredGallery.asset.image_url}
                    width={featuredGallery.asset.width || 900}
                    height={featuredGallery.asset.height || 600}
                    fetchPriority="high"
                    alt="Anonymous GPS art route shared in the public gallery"
                  />
                  <span className="studio-map-expand">Explore the details ↗</span>
                </a>
              ) : (
                <div className="studio-map-placeholder" role="status">
                  {featuredGallery.status === "loading"
                    ? "Choosing a route from the public gallery…"
                    : featuredGallery.status === "empty"
                      ? "No community maps have been shared yet. Yours could be the first."
                      : "The public gallery is temporarily unavailable."}
                </div>
              )}
              <figcaption>
                <p>Real community map images, chosen afresh when you open the page.
                  Always review crossings and access before heading out.</p>
                <div className="studio-art-caption">
                  <a href="#gallery">Browse the public gallery</a>
                  <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>
                </div>
              </figcaption>
            </figure>
        </section>
        )}

        {appView === "planner" && loading && (
          <LoadingState
            onCancel={cancelGeneration}
            kind={loadingKind}
            focusRef={loadingRef}
            progress={generationProgress}
            preview={generationPreview}
          />
        )}

        {appView === "planner" && error && (
          <section className="error-card" role="alert" tabIndex="-1" ref={errorRef}>
            <div className="error-symbol" aria-hidden="true">
              !
            </div>
            <div>
              <h2>
                {error.category === "busy"
                  ? "Route planner is busy"
                  : error.retryable
                    ? "Route planner temporarily unavailable"
                    : "We couldn’t finish this route"}
              </h2>
              <p>{error.message}</p>
              {error.retryable && (
                <p className="error-recovery">
                  Your route idea is still here and nothing was published. You can retry safely.
                  {error.retryAfter ? ` The service suggested waiting ${error.retryAfter} seconds.` : ""}
                </p>
              )}
              {error.requestId && (
                <p className="error-reference">
                  Support reference: <code>{error.requestId}</code>
                </p>
              )}
              <div className="error-actions">
                <button
                  type="button"
                  className="button button--secondary"
                  onClick={() => {
                    const previous = lastGenerationRef.current;
                    generate(previous?.prompt ?? prompt, previous?.payload ?? {});
                  }}
                >
                  Try again
                </button>
                <button
                  type="button"
                  className="button button--quiet"
                  onClick={() => {
                    setError(null);
                    focusPrompt();
                  }}
                >
                  Edit route idea
                </button>
              </div>
            </div>
          </section>
        )}

        {result && (
          // Keep candidate selection and unfinished edits when switching views.
          <div hidden={appView !== "planner" || plannerStep !== "result"}>
            <ResultPanel
              result={result}
              active={appView === "planner" && plannerStep === "result"}
              onDownload={handleDownload}
              onGalleryPublished={(asset) => {
                setLastPublishedGalleryAsset(asset);
                setGalleryRefreshKey((current) => current + 1);
              }}
              onEditRequest={focusPrompt}
              focusRef={resultRef}
              campaignSlug={campaign?.slug ?? null}
              planningMode={planningMode}
              onOpenCampaignBuilder={() => setCampaignBuilderOpen(true)}
            />
          </div>
        )}
        {appView === "gallery" && <GallerySection
          refreshKey={galleryRefreshKey}
          publishedAsset={lastPublishedGalleryAsset}
          campaignSlug={campaign?.slug ?? null}
          onOpenCampaignBuilder={() => setCampaignBuilderOpen(true)}
        />}
      </main>

      <footer>
        <p>© Paceasso</p>
      </footer>
      <div className="sr-only" aria-live="polite">
        {downloadNotice}
      </div>

      <ShapePlacementOverlay
        open={mapPlacementOpen}
        preview={mapPlacementPreview}
        busy={mapPlacementBusy}
        onClose={() => setMapPlacementOpen(false)}
        onConfirm={confirmMapPlacement}
      />

      <CampaignBuilderOverlay
        open={campaignBuilderOpen}
        onClose={() => setCampaignBuilderOpen(false)}
      />
    </div>
  );
}
