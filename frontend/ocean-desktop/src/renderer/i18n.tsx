import {createContext, useContext, useEffect, useMemo, useState} from 'react';

export type UiLanguage = 'en' | 'zh';

type UiLanguageContextValue = {
  language: UiLanguage;
  setLanguage: (language: UiLanguage) => void;
  text: <T>(english: T, chinese: T) => T;
};

const STORAGE_KEY = 'oceanmind.ui-language.v1';
const UiLanguageContext = createContext<UiLanguageContextValue>({
  language: 'en',
  setLanguage: () => undefined,
  text: <T,>(english: T) => english,
});

export function UiLanguageProvider({children}: {children: React.ReactNode}): React.JSX.Element {
  const [language, setLanguage] = useState<UiLanguage>(() => window.localStorage.getItem(STORAGE_KEY) === 'zh' ? 'zh' : 'en');
  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, language);
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
  }, [language]);
  const value = useMemo<UiLanguageContextValue>(() => ({
    language,
    setLanguage,
    text: <T,>(english: T, chinese: T) => language === 'zh' ? chinese : english,
  }), [language]);
  return <UiLanguageContext.Provider value={value}>{children}</UiLanguageContext.Provider>;
}

export function useUiLanguage(): UiLanguageContextValue {
  return useContext(UiLanguageContext);
}
