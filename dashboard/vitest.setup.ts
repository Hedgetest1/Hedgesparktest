/**
 * vitest.setup.ts — runs before every component test.
 *
 * Sets up the jsdom environment so Testing Library can render
 * React components against a virtual DOM.
 */
import "@testing-library/jest-dom/vitest";
