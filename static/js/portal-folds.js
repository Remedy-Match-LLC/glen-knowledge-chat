// Fold rules for the client portal. Pure: no DOM, no network. The page glue in
// client-portal.html applies what these return. Spec:
// docs/superpowers/specs/2026-09-25-portal-folding-design.md
(function(){
  function emptyState(){ return {cards: {}, seen: [], before_fold_all: {}}; }

  function isMap(v){ return !!v && typeof v === 'object' && !Array.isArray(v); }

  function boolMap(v){
    var out = {};
    if(!isMap(v)) return out;
    Object.keys(v).forEach(function(k){ if(typeof v[k] === 'boolean') out[k] = v[k]; });
    return out;
  }

  function normalise(state){
    if(!isMap(state)) return emptyState();
    var bfa = {};
    if(isMap(state.before_fold_all)){
      Object.keys(state.before_fold_all).forEach(function(d){ bfa[d] = boolMap(state.before_fold_all[d]); });
    }
    return {
      cards: boolMap(state.cards),
      seen: Array.isArray(state.seen) ? state.seen.filter(function(d){ return typeof d === 'string'; }) : [],
      before_fold_all: bfa
    };
  }

  function copy(state){ return normalise(JSON.parse(JSON.stringify(state))); }

  // The first-visit default (first card open, the rest folded) applies only while the
  // page has not been seen. After that an unlisted card is new content and arrives open,
  // and a card inserted above cannot fold the one being read (review, 2026-09-26).
  // openIds: cards that open on the first visit whatever their position. Glen,
  // 2026-09-26, "open action cards": invoices, intake, appointments, the scan report.
  function resolveDoor(state, ids, door, openIds){
    var seen = !!door && state.seen.indexOf(door) !== -1;
    var open = openIds || [];
    var out = {};
    ids.forEach(function(id, i){
      out[id] = Object.prototype.hasOwnProperty.call(state.cards, id) ? state.cards[id]
              : (seen || open.indexOf(id) !== -1 ? false : i > 0);
    });
    return out;
  }

  // Marks the page seen, and writes the first-visit layout into the record so it is
  // fixed from then on.
  function markSeen(state, door, ids, openIds){
    var s = copy(state);
    if(s.seen.indexOf(door) !== -1) return s;
    if(ids){
      var first = resolveDoor(s, ids, door, openIds);
      Object.keys(first).forEach(function(id){ s.cards[id] = first[id]; });
    }
    s.seen.push(door);
    return s;
  }

  function setCard(state, id, folded){
    var s = copy(state);
    s.cards[id] = !!folded;
    return s;
  }

  function foldAll(state, door, ids){
    var s = copy(state);
    if(!s.before_fold_all[door]) s.before_fold_all[door] = resolveDoor(s, ids, door);
    ids.forEach(function(id){ s.cards[id] = true; });
    return s;
  }

  function canRestore(state, door){ return !!(state.before_fold_all && state.before_fold_all[door]); }

  function restore(state, door){
    if(!canRestore(state, door)) return state;
    var s = copy(state);
    var saved = s.before_fold_all[door];
    Object.keys(saved).forEach(function(id){ s.cards[id] = saved[id]; });
    delete s.before_fold_all[door];
    return s;
  }

  function adopt(clientState){ return copy(normalise(clientState)); }

  var api = {emptyState: emptyState, normalise: normalise, resolveDoor: resolveDoor,
             markSeen: markSeen, setCard: setCard, foldAll: foldAll, restore: restore,
             canRestore: canRestore, adopt: adopt};
  if(typeof module !== 'undefined' && module.exports) module.exports = api;
  if(typeof window !== 'undefined') window.PortalFolds = api;
})();
