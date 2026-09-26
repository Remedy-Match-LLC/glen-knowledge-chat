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

  function resolveDoor(state, ids){
    var out = {};
    ids.forEach(function(id, i){
      out[id] = Object.prototype.hasOwnProperty.call(state.cards, id) ? state.cards[id] : i > 0;
    });
    return out;
  }

  function markSeen(state, door){
    var s = copy(state);
    if(s.seen.indexOf(door) === -1) s.seen.push(door);
    return s;
  }

  function setCard(state, id, folded){
    var s = copy(state);
    s.cards[id] = !!folded;
    return s;
  }

  function foldAll(state, door, ids){
    var s = copy(state);
    if(!s.before_fold_all[door]) s.before_fold_all[door] = resolveDoor(s, ids);
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
