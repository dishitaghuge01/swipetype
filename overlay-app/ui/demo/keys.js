/* overlay-app/ui/demo/keys.js
 *
 * Browser-preview-only copy of decoder.geometry.KEY_CENTERS (PRD
 * section 7.5). NEVER used by the production overlay -- Python injects
 * the real KEY_CENTERS via window.swipetype.init() (PRD section 8.6).
 * Exists purely so index.html?demo previews in a plain browser with no
 * daemon, no compositor, no Python running at all.
 *
 * Kept in sync with decoder/geometry.py by scratch/check_ui_keys.py --
 * run that script after touching either file.
 */
window.SWIPETYPE_DEMO_KEYS = {
  "Q": [0.0, 0], "W": [1.0, 0], "E": [2.0, 0], "R": [3.0, 0], "T": [4.0, 0],
  "Y": [5.0, 0], "U": [6.0, 0], "I": [7.0, 0], "O": [8.0, 0], "P": [9.0, 0],

  "A": [0.25, 1], "S": [1.25, 1], "D": [2.25, 1], "F": [3.25, 1], "G": [4.25, 1],
  "H": [5.25, 1], "J": [6.25, 1], "K": [7.25, 1], "L": [8.25, 1],

  "Z": [0.75, 2], "X": [1.75, 2], "C": [2.75, 2], "V": [3.75, 2], "B": [4.75, 2],
  "N": [5.75, 2], "M": [6.75, 2]
};