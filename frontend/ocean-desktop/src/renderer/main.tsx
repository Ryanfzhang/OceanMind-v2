import {createRoot} from 'react-dom/client';
import 'katex/dist/katex.min.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import {App} from './App.js';
import {UiLanguageProvider} from './i18n.js';
import './styles.css';

createRoot(document.getElementById('root')!).render(<UiLanguageProvider><App /></UiLanguageProvider>);
