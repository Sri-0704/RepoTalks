'use client';

import React, { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from 'react';
import { StoredRepo, setActiveRepo as setLocalStorageActiveRepo } from '@/lib/storage';
import { fetchProjects, selectProject as apiSelectProject, deleteProject as apiDeleteProject } from '@/lib/api';

type ProjectStatus = 'loading' | 'ready' | 'empty' | 'error';

interface ProjectContextType {
  activeRepo: StoredRepo | null;
  projects: StoredRepo[];
  status: ProjectStatus;
  error: string | null;
  switchingProject: boolean;
  selectProject: (repoId: string) => Promise<void>;
  deleteProject: (repoId: string) => Promise<void>;
  refreshProjects: () => Promise<void>;
  setActiveRepoState: (repo: StoredRepo | null) => void;
}

const ProjectContext = createContext<ProjectContextType | undefined>(undefined);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [activeRepo, setActiveRepoStateInternal] = useState<StoredRepo | null>(null);
  const [projects, setProjects] = useState<StoredRepo[]>([]);
  const [status, setStatus] = useState<ProjectStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [switchingProject, setSwitchingProject] = useState<boolean>(false);
  const selectRequestIdRef = useRef(0);

  const setActiveRepoState = useCallback((repo: StoredRepo | null) => {
    setActiveRepoStateInternal(repo);
    setLocalStorageActiveRepo(repo);
  }, []);

  const refreshProjects = useCallback(async () => {
    setStatus('loading');
    setError(null);
    try {
      const data = await fetchProjects();
      const projectList: StoredRepo[] = data.projects || [];
      setProjects(projectList);

      if (data.active_project) {
        setActiveRepoStateInternal(data.active_project);
        setLocalStorageActiveRepo(data.active_project);
      } else if (projectList.length > 0) {
        setActiveRepoStateInternal(projectList[0]);
        setLocalStorageActiveRepo(projectList[0]);
      } else {
        setActiveRepoStateInternal(null);
        setLocalStorageActiveRepo(null);
      }

      setStatus(projectList.length > 0 ? 'ready' : 'empty');
    } catch (err: any) {
      // UX-05: Preserve last known data on error with stale indication
      setError(err.message || 'Failed to load projects');
      setStatus('error');
      // Do NOT clear projects/activeRepo — keep last known good state
    }
  }, []);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  const selectProject = useCallback(async (repoId: string) => {
    const reqId = ++selectRequestIdRef.current;
    setSwitchingProject(true);
    setError(null);
    try {
      const res = await apiSelectProject(repoId);
      if (reqId !== selectRequestIdRef.current) {
        return;
      }
      if (res.active_project) {
        setActiveRepoState(res.active_project);
      }
      setProjects((prev) =>
        prev.map((p) => ({
          ...p,
          is_active: p.repo_id === repoId,
        }))
      );
    } catch (err: any) {
      if (reqId !== selectRequestIdRef.current) return;
      setError('Failed to select project: ' + err.message);
    } finally {
      if (reqId === selectRequestIdRef.current) {
        setSwitchingProject(false);
      }
    }
  }, [setActiveRepoState]);

  const deleteProject = useCallback(async (repoId: string) => {
    setError(null);
    try {
      const res = await apiDeleteProject(repoId);
      if (res.active_project) {
        setActiveRepoState(res.active_project);
      } else {
        setActiveRepoState(null);
      }
      await refreshProjects();
    } catch (err: any) {
      setError('Failed to delete project: ' + err.message);
    }
  }, [setActiveRepoState, refreshProjects]);

  return (
    <ProjectContext.Provider
      value={{
        activeRepo,
        projects,
        status,
        error,
        switchingProject,
        selectProject,
        deleteProject,
        refreshProjects,
        setActiveRepoState,
      }}
    >
      {children}
    </ProjectContext.Provider>
  );
};

export const useProject = (): ProjectContextType => {
  const context = useContext(ProjectContext);
  if (!context) {
    throw new Error('useProject must be used within a ProjectProvider');
  }
  return context;
};
