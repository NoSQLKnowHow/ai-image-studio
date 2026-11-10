import { createContext, useContext } from "react";
import { NO_TITLES, type EngineTitles } from "./loadProgress";

// The names of the speech engines, for the places that say which model is loading (DESIGN.md section 39.7). App fills it from `capabilities.speech.engines`; without it (before
// the capabilities have arrived, or in a test that does not care) a speech model is "the speech model", which is true and says less.
export const EngineTitlesContext = createContext<EngineTitles>(NO_TITLES);
export const useEngineTitles = (): EngineTitles => useContext(EngineTitlesContext);
